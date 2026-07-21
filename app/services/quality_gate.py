"""YouTube 호출 전에 완성 영상과 제작 계약을 함께 검사한다."""
from __future__ import annotations

import hashlib
import json
import re
import os
from pathlib import Path

from app.services.media_probe import ffprobe_path_for, probe_video, validate_sample


def _spoken_title(title: str) -> str:
    return re.sub(r"[?!。]+$", "", (title or "").strip()).strip()


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def validate_upload_package(work_dir: Path, ffmpeg_path: str) -> dict:
    work_dir = Path(work_dir)
    script_path = work_dir / "script.json"
    produce_path = work_dir / "produce_log.json"
    output_path = work_dir / "output.mp4"
    for required in (script_path, produce_path, output_path):
        if not required.is_file():
            raise RuntimeError(f"업로드 품질검사 필수 파일 없음: {required.name}")

    script_bytes = script_path.read_bytes()
    script = json.loads(script_bytes.decode("utf-8"))
    produce = json.loads(produce_path.read_text(encoding="utf-8"))
    report = probe_video(output_path, ffprobe_path_for(ffmpeg_path))
    failures = validate_sample(report)

    expected_hash = hashlib.sha256(script_bytes).hexdigest()
    if produce.get("script_sha256") != expected_hash:
        failures.append("script_hash")

    if (produce.get("intro") or {}).get("text") != _spoken_title(script.get("title", "")):
        failures.append("intro_text")

    cta_log = produce.get("cta") or {}
    cta_text = (script.get("cta") or "").strip()
    close_text = ""
    if script.get("scenes"):
        close_text = (script["scenes"][-1].get("narration") or "").strip()
    close_has_cta = "구독" in close_text and "좋아요" in close_text
    embedded = bool(cta_log.get("embedded_in_body"))
    separate_audio = float(cta_log.get("audio_duration") or 0) > 0
    if (embedded or close_has_cta) and separate_audio:
        failures.append("cta_duplicate")
    if separate_audio and cta_log.get("text") != cta_text:
        failures.append("cta_text")
    if separate_audio and not ("구독" in cta_text and "좋아요" in cta_text):
        failures.append("cta_actions")

    result = {
        "passed": not failures,
        "failures": list(dict.fromkeys(failures)),
        "report": report,
    }
    produce["quality_gate"] = result
    _atomic_json(produce_path, produce)
    if result["failures"]:
        raise RuntimeError(
            "업로드 품질검사 실패: " + ", ".join(result["failures"])
        )
    return result

