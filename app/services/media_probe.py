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
    audio_duration = float(audio.get("duration") or duration if audio else 0)

    black = run_checked(
        [
            _ffmpeg_path_for(ffprobe_path), "-hide_banner", "-i", str(path),
            "-vf", "blackdetect=d=0.5:pix_th=0.10",
            "-af", "silencedetect=noise=-45dB:d=1.2",
            "-f", "null", os.devnull,
        ],
        timeout=_probe_timeout(),
        text=True,
    )
    black_durations = [
        float(value) for value in re.findall(r"black_duration:([0-9.]+)", black.stderr or "")
    ]
    black_ratio = sum(black_durations) / duration if duration else 1.0
    silence_starts = [
        float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", black.stderr or "")
    ]
    silence_ends = [
        float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", black.stderr or "")
    ]
    silence_durations = [
        float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", black.stderr or "")
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
        "black_ratio": round(black_ratio, 4),
        "audio_duration": round(audio_duration, 3),
        "duration_delta": round(abs(duration - audio_duration), 3),
        "internal_silence_max": round(max(internal_silences, default=0.0), 3),
    }


def probe_ai_video(path: Path, ffprobe_path: str = "ffprobe") -> dict:
    """무음 AI 오프닝 검증에 필요한 영상 스트림 정보만 읽는다."""
    result = run_checked(
        [
            ffprobe_path, "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(Path(path)),
        ],
        timeout=_probe_timeout(),
        text=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration": round(duration, 3),
        "video_codec": video.get("codec_name", ""),
        "has_audio": audio is not None,
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
    if float(report.get("duration_delta", 0)) > 0.5:
        failures.append("audio_duration_delta")
    if float(report.get("internal_silence_max", 0)) >= 1.2:
        failures.append("internal_silence")
    return failures
