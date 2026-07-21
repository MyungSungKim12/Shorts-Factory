"""완성 MP4의 규격과 검은 화면 비율 검사."""
import json
import os
import re
import subprocess
from pathlib import Path

from app.services.process_runner import run_checked


def _probe_timeout() -> int:
    try:
        value = int(os.getenv("MEDIA_PROBE_TIMEOUT_SEC", "180"))
        return value if value > 0 else 180
    except ValueError:
        return 180


def ffprobe_path_for(ffmpeg_path: str) -> str:
    lower = ffmpeg_path.lower()
    if lower.endswith("ffmpeg.exe"):
        return ffmpeg_path[:-10] + "ffprobe.exe"
    if lower.endswith("ffmpeg"):
        return ffmpeg_path[:-6] + "ffprobe"
    return "ffprobe"


def _ffmpeg_path_for(ffprobe_path: str) -> str:
    lower = ffprobe_path.lower()
    if lower.endswith("ffprobe.exe"):
        return ffprobe_path[:-11] + "ffmpeg.exe"
    if lower.endswith("ffprobe"):
        return ffprobe_path[:-7] + "ffmpeg"
    return "ffmpeg"


def probe_video(path: Path, ffprobe_path: str = "ffprobe") -> dict:
    """ffprobe와 blackdetect 결과를 정규화한 검사 보고서를 반환한다."""
    path = Path(path)
    result = run_checked(
        [ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        timeout=_probe_timeout(),
        text=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)

    black = run_checked(
        [
            _ffmpeg_path_for(ffprobe_path), "-hide_banner", "-i", str(path),
            "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", os.devnull,
        ],
        timeout=_probe_timeout(),
        text=True,
    )
    black_durations = [
        float(value) for value in re.findall(r"black_duration:([0-9.]+)", black.stderr or "")
    ]
    black_ratio = sum(black_durations) / duration if duration else 1.0
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration": round(duration, 3),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "has_audio": bool(audio),
        "black_ratio": round(black_ratio, 4),
    }


def validate_sample(report: dict) -> list[str]:
    failures = []
    if (report.get("width"), report.get("height")) != (1080, 1920):
        failures.append("resolution")
    if not 60 <= float(report.get("duration", 0)) <= 75:
        failures.append("duration")
    if report.get("video_codec") != "h264":
        failures.append("video_codec")
    if not report.get("has_audio") or report.get("audio_codec") != "aac":
        failures.append("audio")
    if float(report.get("black_ratio", 1)) > 0.10:
        failures.append("black_frames")
    return failures
