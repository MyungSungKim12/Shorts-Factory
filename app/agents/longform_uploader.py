"""수동 검토 완료 롱폼 영상 YouTube 업로더."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from googleapiclient.http import MediaFileUpload

from app.agents.uploader import (
    _description_with_hashtags,
    _description_with_wikimedia_credits,
    _get_youtube_client,
)
from app.models import validate_longform_script, validate_public_title
from app.services.media_probe import ffprobe_path_for
from app.services.process_runner import run_checked


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _probe_longform_video(path: Path, ffprobe_path: str) -> dict:
    ffprobe = run_checked(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        timeout=180,
        text=True,
    )
    data = json.loads(ffprobe.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = float(
        (data.get("format") or {}).get("duration")
        or video.get("duration")
        or 0
    )
    audio_duration = float(audio.get("duration") or duration if audio else 0)
    ffmpeg_path = ffprobe_path[:-7] + "ffmpeg" if ffprobe_path.endswith("ffprobe") else "ffmpeg"
    if ffprobe_path.endswith("ffprobe.exe"):
        ffmpeg_path = ffprobe_path[:-11] + "ffmpeg.exe"
    audio_check = run_checked(
        [
            ffmpeg_path,
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-45dB:d=1.5",
            "-f",
            "null",
            os.devnull,
        ],
        timeout=180,
        text=True,
    )
    silence_starts = [
        float(value)
        for value in re.findall(r"silence_start:\s*([0-9.]+)", audio_check.stderr or "")
    ]
    silence_ends = [
        float(value)
        for value in re.findall(r"silence_end:\s*([0-9.]+)", audio_check.stderr or "")
    ]
    silence_durations = [
        float(value)
        for value in re.findall(r"silence_duration:\s*([0-9.]+)", audio_check.stderr or "")
    ]
    internal_silences = [
        silence_duration
        for start, end, silence_duration in zip(
            silence_starts, silence_ends, silence_durations
        )
        if start > 0.25 and end < duration - 0.25
    ]
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration": round(duration, 3),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "has_audio": bool(audio),
        "audio_duration": round(audio_duration, 3),
        "duration_delta": round(abs(duration - audio_duration), 3),
        "internal_silence_max": round(max(internal_silences, default=0.0), 3),
    }


def _validate_longform_upload_package(work_dir: Path, ffmpeg_path: str) -> dict:
    work_dir = Path(work_dir)
    script_path = work_dir / "script.json"
    produce_path = work_dir / "produce_log.json"
    output_path = work_dir / "output.mp4"
    for required in (script_path, produce_path, output_path):
        if not required.is_file():
            raise RuntimeError(f"롱폼 업로드 필수 파일 없음: {required.name}")

    validate_longform_script(json.loads(script_path.read_text(encoding="utf-8")))
    report = _probe_longform_video(output_path, ffprobe_path_for(ffmpeg_path))
    failures = []
    duration = float(report.get("duration") or 0)
    if not 240 <= duration <= 600:
        failures.append("duration")
    if (report.get("width"), report.get("height")) != (1920, 1080):
        failures.append("resolution")
    if report.get("video_codec") != "h264":
        failures.append("video_codec")
    if not report.get("has_audio") or report.get("audio_codec") != "aac":
        failures.append("audio")
    if float(report.get("duration_delta") or 0) > 0.75:
        failures.append("audio_duration_delta")
    if float(report.get("internal_silence_max") or 0) >= 1.5:
        failures.append("internal_silence")

    result = {"passed": not failures, "failures": failures, "report": report}
    if failures:
        raise ValueError(f"롱폼 업로드 품질검사 실패: {', '.join(failures)}")
    return result


def _uses_synthetic_longform_media(produce_log: dict) -> bool:
    ai_assets = produce_log.get("ai_assets")
    if isinstance(ai_assets, list) and ai_assets:
        return True
    for source in produce_log.get("media_sources") or []:
        if isinstance(source, dict) and str(source.get("provider") or "").startswith("vertex"):
            return True
    return False


def run_longform_uploader(data_dir: Path, run_id: str) -> dict:
    """Upload `data/longform/{run_id}/output.mp4` and write upload_log.json."""
    data_dir = Path(data_dir)
    work_dir = data_dir / "longform" / run_id
    upload_log = work_dir / "upload_log.json"
    if upload_log.is_file():
        previous = json.loads(upload_log.read_text(encoding="utf-8"))
        if previous.get("status") == "uploaded":
            return {
                "status": "skipped",
                "reason": "이미 업로드됨",
                "video_id": previous.get("video_id"),
                "url": previous.get("url"),
            }

    script_path = work_dir / "script.json"
    produce_path = work_dir / "produce_log.json"
    output_path = work_dir / "output.mp4"
    if not script_path.is_file():
        raise FileNotFoundError(f"롱폼 script.json이 없습니다: {script_path}")
    if not produce_path.is_file():
        raise FileNotFoundError(f"롱폼 produce_log.json이 없습니다: {produce_path}")
    if not output_path.is_file():
        raise FileNotFoundError(f"롱폼 output.mp4가 없습니다: {output_path}")

    script = validate_longform_script(
        json.loads(script_path.read_text(encoding="utf-8"))
    )
    produce = json.loads(produce_path.read_text(encoding="utf-8"))
    quality = _validate_longform_upload_package(
        work_dir,
        os.getenv("FFMPEG_PATH", "ffmpeg"),
    )
    title = validate_public_title(script.get("title", ""))
    tags = []
    total_len = 0
    for tag in script.get("tags", []):
        clean = str(tag).strip()
        if not clean:
            continue
        if total_len + len(clean) > 480:
            break
        tags.append(clean)
        total_len += len(clean)

    description = _description_with_hashtags(
        script.get("description", ""),
        script.get("tags", []),
        max_hashtags=8,
    )
    description = _description_with_wikimedia_credits(
        description,
        produce.get("media_sources", []),
    )
    youtube = _get_youtube_client()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:30],
            "categoryId": os.getenv("LONGFORM_CATEGORY_ID", "24"),
            "defaultLanguage": "ko",
        },
        "status": {
            "privacyStatus": os.getenv(
                "LONGFORM_UPLOAD_PRIVACY",
                os.getenv("UPLOAD_PRIVACY", "unlisted"),
            ),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": _uses_synthetic_longform_media(produce),
        },
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(output_path), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    result = {
        "status": "uploaded",
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "privacy": body["status"]["privacyStatus"],
        "uploaded_at": datetime.now().isoformat(),
        "quality_gate": quality,
    }
    _atomic_json(upload_log, result)
    return result
