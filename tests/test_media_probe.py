"""완성 샘플 미디어 검사 규칙 테스트."""
import json

from app.services import media_probe
from app.services.media_probe import ffprobe_path_for, validate_sample


def test_ffprobe_path_is_derived_from_ffmpeg():
    assert ffprobe_path_for("ffmpeg") == "ffprobe"
    assert ffprobe_path_for(r"C:\tools\ffmpeg.exe") == r"C:\tools\ffprobe.exe"


def test_default_short_duration_ceiling_is_90_seconds(monkeypatch):
    monkeypatch.delenv("MAX_VIDEO_SEC", raising=False)
    assert media_probe.shorts_max_duration() == 90


def test_valid_story_video_is_accepted():
    report = {
        "width": 1080, "height": 1920, "duration": 66.2,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 66.1, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
    }
    assert validate_sample(report) == []


def test_short_valid_video_is_accepted_without_content_minimum():
    report = {
        "width": 1080, "height": 1920, "duration": 30.0,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.0,
        "audio_duration": 29.9, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
    }
    assert validate_sample(report) == []


def test_story_video_over_target_is_accepted_under_short_limit():
    report = {
        "width": 1080, "height": 1920, "duration": 77.0,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 76.9, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
    }
    assert validate_sample(report) == []


def test_story_video_over_short_limit_is_rejected():
    report = {
        "width": 1080, "height": 1920, "duration": 180.1,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 180.0, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
    }
    assert "duration" in validate_sample(report)


def test_invalid_video_lists_every_failure():
    report = {
        "width": 720, "height": 1280, "duration": 0,
        "video_codec": "vp9", "audio_codec": "", "has_audio": False,
        "black_ratio": 0.4,
        "audio_duration": 0, "duration_delta": 2,
        "internal_silence_max": 1.5,
    }
    failures = validate_sample(report)
    assert {
        "resolution", "duration", "video_codec", "audio", "black_frames",
        "audio_duration_delta", "internal_silence",
    } <= set(failures)


def test_probe_reports_internal_silence_and_stream_duration_delta(tmp_path, monkeypatch):
    outputs = iter([
        type("Result", (), {"stdout": json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080,
                 "height": 1920, "duration": "66.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "65.7"},
            ],
            "format": {"duration": "66.0"},
        }), "stderr": "", "returncode": 0})(),
        type("Result", (), {
            "stdout": "", "returncode": 0,
            "stderr": (
                "black_duration:1.0\n"
                "silence_start: 12.0\n"
                "silence_end: 13.4 | silence_duration: 1.4\n"
                "Summary:\n\n"
                "  Integrated loudness:\n"
                "    I:         -16.2 LUFS\n"
                "  Loudness range:\n"
                "    LRA:         4.8 LU\n"
                "  True peak:\n"
                "    Peak:       -1.1 dBFS\n"
                "lavfi.signalstats.YAVG=55.0\n"
                "lavfi.signalstats.YAVG=34.0\n"
                "lavfi.signalstats.YAVG=52.0\n"
            ),
        })(),
    ])
    monkeypatch.setattr(media_probe, "run_checked", lambda *args, **kwargs: next(outputs))

    report = media_probe.probe_video(tmp_path / "output.mp4", "ffprobe")

    assert report["audio_duration"] == 65.7
    assert report["duration_delta"] == 0.3
    assert report["internal_silence_max"] == 1.4
    assert report["integrated_loudness_lufs"] == -16.2
    assert report["loudness_range_lu"] == 4.8
    assert report["true_peak_dbfs"] == -1.1
    assert report["dark_content_ratio"] == 0.3333
    assert report["max_dark_content_seconds"] == 1.0


def test_story_video_with_large_loudness_range_is_rejected():
    report = {
        "width": 1080, "height": 1920, "duration": 66.2,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 66.1, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
        "loudness_range_lu": 10.1,
    }

    assert "audio_loudness_range" in validate_sample(report)


def test_story_video_with_long_dark_content_is_rejected():
    report = {
        "width": 1080, "height": 1920, "duration": 66.2,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 66.1, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
        "dark_content_ratio": 0.35,
        "max_dark_content_seconds": 8.0,
    }

    assert "dark_content" in validate_sample(report)


def test_story_video_allows_short_dark_transition():
    report = {
        "width": 1080, "height": 1920, "duration": 66.2,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
        "audio_duration": 66.1, "duration_delta": 0.1,
        "internal_silence_max": 0.0,
        "dark_content_ratio": 0.04,
        "max_dark_content_seconds": 2.0,
    }

    assert "dark_content" not in validate_sample(report)
