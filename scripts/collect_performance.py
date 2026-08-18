"""자동 제작 파이프라인과 독립적으로 영상 성과를 수집한다."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.services import performance_store as store  # noqa: E402
from app.services.youtube_performance import (  # noqa: E402
    fetch_owner_metrics,
    fetch_public_statistics,
    fetch_retention,
)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _video_start_date(video: dict) -> date:
    uploaded = _parse_datetime(video.get("uploaded_at"))
    if uploaded is not None:
        return uploaded.date()
    run_id = str(video.get("date") or "")
    if re.fullmatch(r"\d{8}(?:-[1-6])?", run_id):
        return datetime.strptime(run_id[:8], "%Y%m%d").date()
    return date(2020, 1, 1)


def _analytics_end_date(now: datetime) -> date:
    return now.astimezone(timezone.utc).date() - timedelta(days=1)


def _safe_error(prefix: str, exc: Exception) -> str:
    message = " ".join(str(exc).split())[:240]
    message = re.sub(r"(?i)(token|key|secret)=?[^\s&]*", r"\1=[숨김]", message)
    return f"{prefix}: {message or type(exc).__name__}"


def _merge_metrics(
    videos: list[dict],
    public: dict[str, dict],
    owner: dict[str, dict],
    now: datetime,
) -> list[dict]:
    rows = []
    current = now.astimezone(timezone.utc)
    for video in videos:
        video_id = str(video["video_id"])
        public_row = public.get(video_id)
        owner_row = owner.get(video_id)
        if public_row is None and owner_row is None:
            continue
        uploaded = _parse_datetime(video.get("uploaded_at"))
        age_hours = (
            max(0.0, (current - uploaded).total_seconds() / 3600)
            if uploaded is not None
            else None
        )
        merged = dict(owner_row or {})
        for key in ("views", "likes", "comments"):
            if public_row is not None and public_row.get(key) is not None:
                merged[key] = public_row[key]
        merged.update(
            {
                "video_id": video_id,
                "snapshot_at": current.isoformat(),
                "age_hours": round(age_hours, 3) if age_hours is not None else None,
                "source_status": (
                    "complete"
                    if public_row is not None and owner_row is not None
                    else "public_only"
                    if public_row is not None
                    else "analytics_only"
                ),
            }
        )
        rows.append(merged)
    return rows


def _write_json_atomically(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def collect_performance(
    data_dir: Path,
    *,
    now: datetime | None = None,
    public_fetcher: Callable = fetch_public_statistics,
    owner_fetcher: Callable = fetch_owner_metrics,
    retention_fetcher: Callable = fetch_retention,
) -> dict:
    data_dir = Path(data_dir)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    store.init_performance_schema(data_dir)
    features_saved = store.capture_video_features(data_dir, captured_at=current)
    videos = store.list_uploaded_videos(data_dir)
    report_path = data_dir / "reports" / "performance_latest.json"

    if not videos:
        collection = {
            "status": "success",
            "videos_seen": 0,
            "features_saved": features_saved,
            "videos_saved": 0,
            "retention_videos": 0,
            "errors": [],
            "report_path": str(report_path),
        }
        report = store.build_performance_report(data_dir, current)
        report["collection"] = collection
        _write_json_atomically(report_path, report)
        return collection

    video_ids = [str(video["video_id"]) for video in videos]
    start_date = min(_video_start_date(video) for video in videos)
    end_date = _analytics_end_date(current)
    public: dict[str, dict] = {}
    owner: dict[str, dict] = {}
    errors = []
    public_ok = False
    owner_ok = False

    try:
        public = public_fetcher(video_ids) or {}
        public_ok = True
    except Exception as exc:
        errors.append(_safe_error("public_stats", exc))

    try:
        owner = owner_fetcher(video_ids, start_date, end_date) or {}
        owner_ok = True
    except Exception as exc:
        errors.append(_safe_error("owner_metrics", exc))

    rows = _merge_metrics(videos, public, owner, current)
    videos_saved = store.save_performance_snapshots(data_dir, rows)

    retention_videos = 0
    if owner_ok:
        by_id = {str(video["video_id"]): video for video in videos}
        for video_id in store.retention_due_video_ids(data_dir, current, limit=20):
            try:
                points = retention_fetcher(
                    video_id,
                    _video_start_date(by_id[video_id]),
                    end_date,
                )
                store.save_retention_points(
                    data_dir, video_id, current.date().isoformat(), points
                )
                retention_videos += 1
            except Exception as exc:
                errors.append(_safe_error(f"retention[{video_id}]", exc))

    status = (
        "failed"
        if not public_ok and not owner_ok
        else "partial"
        if errors or not public_ok or not owner_ok
        else "success"
    )
    collection = {
        "status": status,
        "videos_seen": len(videos),
        "features_saved": features_saved,
        "videos_saved": videos_saved,
        "retention_videos": retention_videos,
        "errors": errors,
        "report_path": str(report_path),
    }
    report = store.build_performance_report(data_dir, current)
    report["collection"] = collection
    _write_json_atomically(report_path, report)
    return collection


def main() -> int:
    result = collect_performance(Path(os.getenv("DATA_DIR", "./data")))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
