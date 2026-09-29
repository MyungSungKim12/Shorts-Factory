"""Generate a resumable, budget-capped reusable AI image/video asset bank."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.services.ai_asset_batch import (
    AssetJob,
    build_jobs,
    load_or_create_state,
    load_subject_seeds,
    run_batch,
)
from app.services.ai_opening_library import (
    AiOpeningLibrary,
    build_opening_derivative,
    normalize_subject_key,
    validate_ai_opening,
)
from app.services.credit_guard import credit_status
from app.services.media_library import fetch_required_exact_media
from app.services.vertex_image import generate_visual_asset, visual_asset_prompt
from app.services.vertex_video import (
    _video_cost_usd,
    generate_opening_video,
    motion_prompt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 자산을 예산 한도 내에서 영구 생성합니다.")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--subjects",
        type=Path,
        default=ROOT / "assets" / "ai_asset_subjects.json",
    )
    parser.add_argument("--budget-krw", type=float, default=250_000)
    parser.add_argument("--video-variants", type=int, default=5)
    parser.add_argument("--min-free-gb", type=float, default=12.0)
    parser.add_argument("--ffmpeg-path", default=None)
    return parser


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _ensure_reference(job: AssetJob, bank_root: Path) -> tuple[Path, dict]:
    subject_dir = bank_root / job.subject.key
    reference = subject_dir / "reference.jpg"
    metadata_file = subject_dir / "reference.json"
    metadata = _read_json(metadata_file)
    if reference.is_file() and reference.stat().st_size > 0 and metadata:
        return reference, metadata
    identity = {
        "required_exact": True,
        "exact_queries": [f"exact:{job.subject.exact_query}"],
    }
    reference, metadata = fetch_required_exact_media(identity, reference, set())
    _write_json(metadata_file, metadata)
    return reference, metadata


def _generate_image(job: AssetJob, bank_root: Path) -> tuple[str, dict]:
    subject_dir = bank_root / job.subject.key
    output = subject_dir / "ai-image-vertical.jpg"
    context = (
        f"{job.subject.category} 소재인 {job.subject.title_ko}. "
        "쇼츠 도입부와 설명 장면에 쓸 사실적인 합성 보조 화면"
    )
    result = generate_visual_asset(
        output,
        subject=job.subject.title_ko,
        context=context,
        aspect_ratio="9:16",
        run_id=f"asset-bank:{job.job_id}",
    )
    metadata = {
        "asset_type": "synthetic_supporting_image",
        "subject_key": job.subject.key,
        "title_ko": job.subject.title_ko,
        "exact_query": job.subject.exact_query,
        "category": job.subject.category,
        "path": str(output),
        "model": result.model,
        "estimated_cost_usd": result.estimated_cost_usd,
        "prompt": visual_asset_prompt(subject=job.subject.title_ko, context=context),
        "reuse_scope": "concept",
        "disclosure": "AI-generated supporting visual; not factual evidence",
    }
    _write_json(subject_dir / "ai-image-vertical.json", metadata)
    return "completed", metadata


def _generate_video(
    job: AssetJob,
    *,
    data_dir: Path,
    bank_root: Path,
    ffmpeg_path: str,
) -> tuple[str, dict]:
    reference, source_metadata = _ensure_reference(job, bank_root)
    library = AiOpeningLibrary(data_dir)
    subject_key = normalize_subject_key(job.subject.exact_query)
    asset_id, asset_dir = library.create_asset_workspace(subject_key)
    stored_reference = asset_dir / "reference.jpg"
    master = asset_dir / "master.mp4"
    opening = asset_dir / "opening.mp4"
    shutil.copy2(reference, stored_reference)
    model = ""
    validation = {}
    try:
        generated = generate_opening_video(
            stored_reference,
            master,
            job.subject.exact_query,
        )
        model = generated.model
        validation = validate_ai_opening(
            stored_reference, master, ffmpeg_path=ffmpeg_path
        )
        if not validation.get("passed"):
            raise ValueError(
                "AI opening validation failed: "
                + ", ".join(validation.get("failures") or ["unknown"])
            )
        build_opening_derivative(master, opening, ffmpeg_path=ffmpeg_path)
        asset = library.register_asset(metadata={
            "asset_id": asset_id,
            "subject_key": subject_key,
            "reuse_scope": "exact_subject",
            "status": "ready",
            "reference_path": str(stored_reference),
            "master_path": str(master),
            "opening_path": str(opening),
            "source_url": source_metadata.get("source_url", ""),
            "license": source_metadata.get("license", ""),
            "source_metadata": source_metadata,
            "model": model,
            "prompt": motion_prompt(job.subject.exact_query),
            "validation": validation,
        })
        return "completed", {
            "asset_id": asset.asset_id,
            "subject_key": asset.subject_key,
            "master_path": str(asset.master_path),
            "opening_path": str(asset.opening_path),
            "model": model,
            "estimated_cost_usd": generated.estimated_cost_usd,
            "source_url": asset.source_url,
        }
    except Exception as exc:
        error = " ".join(str(exc).split())[:1000]
        library.register_asset(metadata={
            "asset_id": asset_id,
            "subject_key": subject_key,
            "reuse_scope": "exact_subject",
            "status": "rejected" if validation else "generating",
            "reference_path": str(stored_reference),
            "master_path": str(master),
            "opening_path": str(opening),
            "source_url": source_metadata.get("source_url", ""),
            "license": source_metadata.get("license", ""),
            "source_metadata": source_metadata,
            "model": model,
            "prompt": motion_prompt(job.subject.exact_query),
            "validation": validation,
            "error": error,
        })
        if validation:
            return "rejected", {"asset_id": asset_id, "error": error}
        raise


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir or os.getenv("DATA_DIR", "./data"))
    os.environ["DATA_DIR"] = str(data_dir)
    ffmpeg_path = args.ffmpeg_path or os.getenv("FFMPEG_PATH", "ffmpeg")
    subjects = load_subject_seeds(args.subjects)
    bank_root = data_dir / "media" / "ai_asset_bank"
    bank_root.mkdir(parents=True, exist_ok=True)
    state_path = bank_root / "batch_state.json"
    current_credit = credit_status(data_dir)
    state = load_or_create_state(
        state_path,
        budget_krw=max(0.0, args.budget_krw),
        committed_start_krw=float(current_credit["committed_krw"]),
    )
    rate = max(0.0, float(os.getenv("CLOUD_USD_TO_KRW", "1400")))
    image_cost = max(0.0, float(os.getenv("IMAGEN_THUMBNAIL_COST_USD", "0.04"))) * rate
    video_cost = _video_cost_usd(
        os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001"),
        4,
        os.getenv("VEO_RESOLUTION", "720p").strip().lower(),
    ) * rate
    jobs = build_jobs(
        subjects,
        video_variants=args.video_variants,
        image_estimate_krw=image_cost,
        video_estimate_krw=video_cost,
    )

    def status_provider() -> dict:
        status = credit_status(data_dir)
        status["free_bytes"] = shutil.disk_usage(data_dir).free
        return status

    def executor(job: AssetJob) -> tuple[str, dict]:
        print(f"START {job.job_id} estimated={job.estimated_krw:.0f}KRW", flush=True)
        if job.kind == "image":
            result = _generate_image(job, bank_root)
        else:
            result = _generate_video(
                job,
                data_dir=data_dir,
                bank_root=bank_root,
                ffmpeg_path=ffmpeg_path,
            )
        print(f"END {job.job_id} status={result[0]}", flush=True)
        return result

    result = run_batch(
        jobs,
        state_path=state_path,
        state=state,
        status_provider=status_provider,
        executor=executor,
        min_free_bytes=int(max(0.0, args.min_free_gb) * 1024**3),
    )
    print(json.dumps({
        "stop_reason": result.get("stop_reason"),
        "jobs_recorded": len(result.get("jobs") or {}),
        "state_path": str(state_path),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
