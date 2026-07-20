"""완성 샘플 미디어 검사 규칙 테스트."""
from app.services.media_probe import ffprobe_path_for, validate_sample


def test_ffprobe_path_is_derived_from_ffmpeg():
    assert ffprobe_path_for("ffmpeg") == "ffprobe"
    assert ffprobe_path_for(r"C:\tools\ffmpeg.exe") == r"C:\tools\ffprobe.exe"


def test_valid_story_video_is_accepted():
    report = {
        "width": 1080, "height": 1920, "duration": 66.2,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01,
    }
    assert validate_sample(report) == []


def test_invalid_video_lists_every_failure():
    report = {
        "width": 720, "height": 1280, "duration": 50,
        "video_codec": "vp9", "audio_codec": "", "has_audio": False,
        "black_ratio": 0.4,
    }
    failures = validate_sample(report)
    assert {"resolution", "duration", "video_codec", "audio", "black_frames"} <= set(failures)
