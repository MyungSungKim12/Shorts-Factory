"""샘플 생성기가 업로드·운영 데이터와 격리되는지 검증."""
import asyncio

import pytest

from scripts import generate_sample


def test_sample_pipeline_uses_only_samples_tree(tmp_path, monkeypatch):
    seen = {}

    def fake_researcher(data_dir, run_id, **kwargs):
        seen["researcher"] = kwargs
        sample_dir = data_dir / "samples" / run_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        (sample_dir / "topic.json").write_text("{}", encoding="utf-8")
        return {"format": "story"}

    def fake_writer(data_dir, run_id, **kwargs):
        seen["writer"] = kwargs
        (data_dir / "samples" / run_id / "script.json").write_text("{}", encoding="utf-8")
        return {"format": "story"}

    async def fake_producer(data_dir, run_id, ffmpeg_path, work_root="work"):
        seen["producer"] = {"work_root": work_root}
        output = data_dir / work_root / run_id / "output.mp4"
        output.write_bytes(b"video")
        return {"output_file": str(output)}

    monkeypatch.setattr(generate_sample, "run_researcher", fake_researcher)
    monkeypatch.setattr(generate_sample, "run_writer", fake_writer)
    monkeypatch.setattr(generate_sample, "run_story_producer", fake_producer)
    monkeypatch.setattr(generate_sample, "probe_video", lambda *args: {
        "width": 1080, "height": 1920, "duration": 64,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0,
    })

    output = asyncio.run(generate_sample.generate_sample("safe-sample", tmp_path, "ffmpeg"))
    assert output == tmp_path / "samples" / "safe-sample" / "output.mp4"
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "videos.sqlite").exists()
    assert seen["researcher"] == {
        "recent_topics": [], "content_format": "story",
        "work_root": "samples", "use_cache": False,
    }
    assert seen["writer"] == {"content_format": "story", "work_root": "samples"}
    assert seen["producer"] == {"work_root": "samples"}


def test_sample_id_cannot_escape_samples_directory(tmp_path):
    with pytest.raises(ValueError, match="sample_id"):
        asyncio.run(generate_sample.generate_sample("../escape", tmp_path, "ffmpeg"))


def test_validation_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_sample, "run_researcher", lambda *args, **kwargs: {})
    monkeypatch.setattr(generate_sample, "run_writer", lambda *args, **kwargs: {})

    async def fake_producer(data_dir, run_id, ffmpeg_path, work_root="work"):
        output = data_dir / work_root / run_id / "output.mp4"
        output.write_bytes(b"bad")
        return {"output_file": str(output)}

    monkeypatch.setattr(generate_sample, "run_story_producer", fake_producer)
    monkeypatch.setattr(generate_sample, "probe_video", lambda *args: {
        "width": 720, "height": 1280, "duration": 45,
        "video_codec": "h264", "audio_codec": "", "has_audio": False,
        "black_ratio": 0.5,
    })
    with pytest.raises(RuntimeError, match="샘플 자동 검증 실패"):
        asyncio.run(generate_sample.generate_sample("bad-sample", tmp_path, "ffmpeg"))
