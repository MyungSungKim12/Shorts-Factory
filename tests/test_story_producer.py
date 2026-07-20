"""스토리 샷 계획, 렌더링 필터, 프로듀서 라우팅 테스트."""
import asyncio
import pytest

from app.agents import producer
from app.agents import story_producer


def test_story_cta_keeps_topic_aware_copy_with_both_actions():
    value, fallback = story_producer.normalize_story_cta(
        "이런 자연의 비밀이 더 궁금하다면 구독과 좋아요 부탁드립니다."
    )
    assert value.startswith("이런 자연의 비밀")
    assert fallback is False


@pytest.mark.parametrize("value", ["", "다음 이야기도 구독해 주세요.", "좋아요 부탁드립니다."])
def test_story_cta_falls_back_when_an_action_is_missing(value):
    normalized, fallback = story_producer.normalize_story_cta(value)
    assert normalized == "이런 이야기가 더 궁금하다면, 구독과 좋아요 부탁드립니다."
    assert fallback is True


def test_each_story_beat_becomes_short_visual_shots():
    script = {"scenes": [{
        "n": 1,
        "role": "hook",
        "narration": "비가 없는데 물이 남아 있습니다.",
        "visuals": ["desert lake aerial", "cracked desert ground"],
        "duration_sec": 8,
        "emphasis": ["물이 남아 있습니다"],
    }]}
    shots = story_producer.build_shot_plan(script)
    assert len(shots) == 2
    assert all(2 <= shot["duration_sec"] <= 4 for shot in shots)
    assert {shot["keyword"] for shot in shots} == {
        "desert lake aerial", "cracked desert ground"
    }
    assert [shot["shot_n"] for shot in shots] == [1, 2]


def test_long_beat_adds_shots_instead_of_exceeding_four_seconds():
    script = {"scenes": [{
        "n": 3, "role": "mechanism", "narration": "긴 설명",
        "visuals": ["underground water", "desert spring"],
        "duration_sec": 15, "emphasis": [],
    }]}
    shots = story_producer.build_shot_plan(script)
    assert len(shots) == 4
    assert all(shot["duration_sec"] <= 4 for shot in shots)
    assert abs(sum(shot["duration_sec"] for shot in shots) - 15) < 0.01


def test_still_image_filter_has_motion_without_ranking_bands():
    vf = story_producer.visual_filter("shot.jpg", duration=3.0)
    assert "zoompan" in vf
    assert "1080x1920" in vf
    assert "pad=1080:1920" not in vf


def test_video_filter_is_full_frame_vertical():
    vf = story_producer.visual_filter("shot.mp4", duration=3.0)
    assert "scale=1080:1920" in vf
    assert "crop=1080:1920" in vf
    assert "zoompan" not in vf


def test_exact_landscape_image_can_preserve_full_composition():
    vf = story_producer.visual_filter("blood-falls.jpg", duration=3.0, preserve_full=True)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "boxblur" in vf
    assert "overlay" in vf
    assert "pad=1080:1920" not in vf
    assert "zoompan" in vf


def test_tts_summary_reports_mixed_provider():
    results = [
        type("R", (), {"provider": "google", "voice": "ko-KR-Neural2-C", "speaking_rate": 1.05})(),
        type("R", (), {"provider": "gtts", "voice": "ko", "speaking_rate": 1.0})(),
    ]
    assert story_producer.summarize_tts(results)["provider"] == "mixed"


def test_subtitles_end_with_actual_audio_instead_of_scene_padding(tmp_path):
    script = {"scenes": [{
        "n": 1,
        "narration": "첫 문장입니다. 두 번째 문장입니다.",
        "duration_sec": 8,
    }]}
    output = tmp_path / "subs.srt"

    story_producer._write_srt(
        script,
        scene_durations={1: 8.0},
        audio_durations={1: 6.0},
        output=output,
    )

    subtitles = output.read_text(encoding="utf-8")
    assert "00:00:06,000" in subtitles
    assert "00:00:08,000" not in subtitles


def test_story_subtitle_style_uses_smaller_font():
    assert "FontSize=16" in story_producer._subtitle_style("Malgun Gothic")


def test_producer_routes_story_without_entering_ranking_renderer(tmp_path, monkeypatch):
    seen = {}

    async def fake_story(data_dir, run_id, ffmpeg_path, work_root="work"):
        seen.update(run_id=run_id, work_root=work_root)
        return {"format": "story", "output_file": "sample.mp4"}

    monkeypatch.setattr(story_producer, "run_story_producer", fake_story)
    result = asyncio.run(producer.run_producer(
        tmp_path, "sample-1", "ffmpeg", content_format="story", work_root="samples"
    ))
    assert result["format"] == "story"
    assert seen == {"run_id": "sample-1", "work_root": "samples"}
