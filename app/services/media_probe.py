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


def shorts_max_duration() -> int:
    """Return the configured channel ceiling within YouTube's 3-minute limit."""
    try:
        configured = int(os.getenv("MAX_VIDEO_SEC", "90"))
    except ValueError:
        configured = 90
    return min(180, max(75, configured))


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
            "-vf", (
                "blackdetect=d=0.5:pix_th=0.10,"
                "crop=1080:1330:0:260,fps=1,signalstats,"
                "metadata=print:key=lavfi.signalstats.YAVG"
            ),
            "-af", "silencedetect=noise=-45dB:d=1.2,ebur128=peak=true",
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
    loudness_text = black.stderr or ""
    integrated_match = re.findall(
        r"Integrated loudness:\s+I:\s*(-?[0-9.]+)\s+LUFS",
        loudness_text,
    )
    range_match = re.findall(
        r"Loudness range:\s+LRA:\s*([0-9.]+)\s+LU",
        loudness_text,
    )
    peak_match = re.findall(
        r"True peak:\s+Peak:\s*(-?[0-9.]+)\s+dBFS",
        loudness_text,
    )
    luma_values = [
        float(value)
        for value in re.findall(
            r"lavfi\.signalstats\.YAVG=([0-9.]+)",
            loudness_text,
        )
    ]
    dark_flags = [value < 40.0 for value in luma_values]
    longest_dark_run = 0
    current_dark_run = 0
    for is_dark in dark_flags:
        current_dark_run = current_dark_run + 1 if is_dark else 0
        longest_dark_run = max(longest_dark_run, current_dark_run)
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
        "integrated_loudness_lufs": (
            float(integrated_match[-1]) if integrated_match else None
        ),
        "loudness_range_lu": float(range_match[-1]) if range_match else None,
        "true_peak_dbfs": float(peak_match[-1]) if peak_match else None,
        "dark_content_ratio": (
            round(sum(dark_flags) / len(dark_flags), 4)
            if dark_flags
            else None
        ),
        "max_dark_content_seconds": (
            float(longest_dark_run) if dark_flags else None
        ),
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
    if not 60 <= float(report.get("duration", 0)) <= shorts_max_duration():
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
    loudness_range = report.get("loudness_range_lu")
    if loudness_range is not None and float(loudness_range) > 10.0:
        failures.append("audio_loudness_range")
    dark_ratio = report.get("dark_content_ratio")
    dark_seconds = report.get("max_dark_content_seconds")
    if (
        dark_ratio is not None
        and dark_seconds is not None
        and (float(dark_ratio) > 0.25 or float(dark_seconds) >= 6.0)
    ):
        failures.append("dark_content")
    return failures
