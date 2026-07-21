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


def test_cta_plan_does_not_append_when_last_scene_already_reads_same_cta():
    cta = "이 신비로운 자연의 비밀이 흥미로우셨다면 구독과 좋아요 부탁드립니다."
    script = {
        "cta": cta,
        "scenes": [{
            "n": 7,
            "role": "close",
            "narration": f"데스밸리의 돌들입니다. {cta}",
        }],
    }

    plan = story_producer.build_story_cta_plan(script)

    assert plan["text"] == cta
    assert plan["embedded_in_body"] is True
    assert plan["append"] is False


def test_cta_plan_appends_when_close_has_no_subscription_request():
    script = {
        "cta": "이번 이야기가 더 궁금하다면 구독과 좋아요 부탁드립니다.",
        "scenes": [{"n": 7, "role": "close", "narration": "대자연이 만든 결과였습니다."}],
    }

    plan = story_producer.build_story_cta_plan(script)

    assert plan["embedded_in_body"] is False
    assert plan["append"] is True


def test_caption_split_never_breaks_korean_word_mid_token():
    chunks = story_producer._split_caption("1년에 300일 동안 번개가 칩니다.", max_len=8)
    assert "300일" in " ".join(chunks).split()
    assert all(chunk not in {"30", "0일"} for chunk in chunks)


def test_caption_keeps_unspaced_long_token_whole():
    token = "초장문공백없는한국어토큰"
    assert story_producer._split_caption(token, max_len=5) == [token]


def test_title_wrap_uses_at_most_two_lines_without_breaking_words():
    title = "1년에 300일 동안 번개가 치는 호수의 비밀"
    lines = story_producer._wrap_title(title, max_chars=12, max_lines=2)
    assert len(lines) == 2
    assert " ".join(lines) == title
    assert "300일" in lines[0] or "300일" in lines[1]


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
    assert len(shots) == 4
    assert all(1.8 <= shot["duration_sec"] <= 2.2 for shot in shots)
    assert {shot["keyword"] for shot in shots} == {
        "desert lake aerial", "cracked desert ground"
    }
    assert [shot["shot_n"] for shot in shots] == [1, 2, 3, 4]


def test_long_beat_adds_shots_instead_of_exceeding_four_seconds():
    script = {"scenes": [{
        "n": 3, "role": "mechanism", "narration": "긴 설명",
        "visuals": ["underground water", "desert spring"],
        "duration_sec": 15, "emphasis": [],
    }]}
    shots = story_producer.build_shot_plan(script)
    assert len(shots) == 5
    assert all(2.2 <= shot["duration_sec"] <= 3.0 for shot in shots)
    assert abs(sum(shot["duration_sec"] for shot in shots) - 15) < 0.01


def test_retention_shot_ranges_are_role_specific():
    assert story_producer._shot_duration_range("hook") == (1.8, 2.2)
    assert story_producer._shot_duration_range("context") == (2.4, 3.2)
    assert story_producer._shot_duration_range("mechanism") == (2.2, 3.0)
    assert story_producer._shot_duration_range("payoff") == (2.0, 2.8)
    assert story_producer._shot_duration_range("close") == (2.5, 3.5)


def test_spoken_intro_keeps_the_complete_topic_phrase():
    assert (
        story_producer._spoken_intro("300일 동안 번개가 멈추지 않는 마을의 비밀")
        == "300일 동안 번개가 멈추지 않는 마을의 비밀"
    )


def test_spoken_intro_never_cuts_a_title_mid_phrase():
    title = "딸기우유 빛깔 호수가 분홍빛을 유지하는 신비로운 이유"
    assert story_producer._spoken_intro(title) == title


def test_still_image_filter_has_motion_without_ranking_bands():
    vf = story_producer.visual_filter("shot.jpg", duration=3.0)
    assert "zoompan" in vf
    assert "1080x1330" in vf
    assert "pad=1080:1920:0:260:black" in vf
    assert "setsar=1" in vf


def test_video_filter_is_full_frame_vertical():
    vf = story_producer.visual_filter("shot.mp4", duration=3.0)
    assert "scale=1124:1383" in vf
    assert "crop=1080:1330" in vf
    assert "pad=1080:1920:0:260:black" in vf
    assert "setsar=1" in vf
    assert "zoompan" not in vf


def test_video_motion_alternates_by_shot_index():
    first = story_producer.visual_filter("shot.mp4", 2.5, motion_index=0)
    second = story_producer.visual_filter("shot.mp4", 2.5, motion_index=1)
    assert "scale=1124:1383" in first
    assert first != second
    assert "crop=1080:1330" in first
    assert "crop=1080:1330" in second


def test_exact_landscape_image_can_preserve_full_composition():
    vf = story_producer.visual_filter("blood-falls.jpg", duration=3.0, preserve_full=True)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "boxblur" in vf
    assert "overlay" in vf
    assert "pad=1080:1920:0:260:black" in vf
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
    style = story_producer._subtitle_style("Malgun Gothic")
    assert "FontSize=16" in style
    assert "MarginV=90" in style


def test_subtitle_style_moves_caption_to_lower_middle():
    style = story_producer._subtitle_style("Malgun Gothic")
    assert "Alignment=2" in style
    assert "MarginV=90" in style


def test_caption_highlights_only_one_number_or_keyword():
    highlighted = story_producer._highlight_caption("무려 300일 동안 번개가 칩니다")
    assert highlighted.count('<font color="#FFD700">') == 1
    assert highlighted.count("</font>") == 1
    assert "300일" in highlighted


def test_missing_source_reuses_last_valid_media(tmp_path):
    previous = tmp_path / "previous.mp4"
    previous.write_bytes(b"valid-media")

    media, metadata, is_new_source = story_producer._resolve_story_media(
        media=None,
        metadata={"provider": "black_bg", "fallback": True},
        last_media=previous,
        last_metadata={"provider": "pexels", "id": "123"},
    )

    assert media == previous
    assert metadata["provider"] == "pexels"
    assert metadata["fallback"] is True
    assert metadata["reused_previous"] is True
    assert is_new_source is False


def test_trim_narration_removes_only_leading_and_trailing_silence(tmp_path, monkeypatch):
    seen = {}
    source = tmp_path / "raw.mp3"
    output = tmp_path / "clean.wav"

    monkeypatch.setattr(
        story_producer,
        "_run_ffmpeg",
        lambda cmd, cwd=None: seen.update(cmd=cmd, cwd=cwd),
    )

    story_producer._trim_narration(source, output, "ffmpeg")

    audio_filter = seen["cmd"][seen["cmd"].index("-af") + 1]
    assert audio_filter.count("silenceremove=start_periods=1") == 2
    assert audio_filter.count("areverse") == 2
    assert "stop_periods" not in audio_filter
    assert seen["cmd"][-1] == str(output)


def test_scene_duration_follows_clean_audio_instead_of_planned_padding():
    assert story_producer._scene_duration(7.0, 5.8) == 5.95


def test_finish_video_normalizes_both_overlay_aspect_ratios(tmp_path, monkeypatch):
    seen = {}

    monkeypatch.setattr(story_producer, "_pick_bgm", lambda: None)
    monkeypatch.setattr(
        story_producer,
        "_run_ffmpeg",
        lambda cmd, cwd=None: seen.update(cmd=cmd, cwd=cwd),
    )

    story_producer._finish_video(
        tmp_path / "concat.mp4",
        tmp_path / "output.mp4",
        tmp_path / "subs.srt",
        tmp_path / "title.png",
        "ffmpeg",
        tmp_path,
    )

    filters = seen["cmd"][seen["cmd"].index("-filter_complex") + 1]
    assert "[0:v]setsar=1,subtitles=" in filters
    assert "[1:v]setsar=1[title]" in filters
    assert "[subbed][title]overlay=0:0" in filters
    title_input = seen["cmd"].index(str(tmp_path / "title.png"))
    assert seen["cmd"][title_input - 3:title_input] == ["-loop", "1", "-i"]
    assert "overlay=0:0:shortest=1" in filters


def test_transition_events_are_limited_to_hook_and_payoff():
    script = {"scenes": [
        {"n": 1, "role": "hook"},
        {"n": 2, "role": "context"},
        {"n": 3, "role": "payoff"},
        {"n": 4, "role": "close"},
    ]}
    assert story_producer._transition_scene_numbers(script) == [1, 3]


def test_finish_video_mixes_two_transition_cues(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(story_producer, "_pick_bgm", lambda: None)
    monkeypatch.setattr(
        story_producer,
        "_run_ffmpeg",
        lambda cmd, cwd=None: seen.update(cmd=cmd, cwd=cwd),
    )

    story_producer._finish_video(
        tmp_path / "concat.mp4",
        tmp_path / "output.mp4",
        tmp_path / "subs.srt",
        tmp_path / "title.png",
        "ffmpeg",
        tmp_path,
        transition_tone=tmp_path / "transition.wav",
        transition_times=[0.0, 12.5],
    )

    filters = seen["cmd"][seen["cmd"].index("-filter_complex") + 1]
    assert "asplit=2" in filters
    assert "adelay=12500:all=1" in filters
    assert "amix=inputs=3" in filters
    assert seen["cmd"][seen["cmd"].index("-map") + 3] == "[finala]"


def test_story_layout_reserves_fixed_title_and_subtitle_bands():
    assert story_producer.STORY_LAYOUT == {
        "canvas_width": 1080,
        "canvas_height": 1920,
        "top_band": 260,
        "video_height": 1330,
        "bottom_band": 330,
    }


def test_title_overlay_is_full_canvas_and_at_most_two_lines(tmp_path):
    output = tmp_path / "title.png"
    metadata = story_producer._create_title_overlay(
        "A surprisingly long fixed story title for shorts", output
    )

    from PIL import Image

    with Image.open(output) as image:
        assert image.size == (1080, 1920)
        assert image.mode == "RGBA"
    assert metadata["line_count"] <= 2
    assert metadata["font_size"] >= 34


def test_cta_timing_uses_measured_audio_after_body():
    timing = story_producer.build_cta_timing(68.5, 3.2)
    assert timing == {"start": 68.5, "end": 71.7, "total_duration": 71.7}


def test_story_timing_places_spoken_title_before_body_and_cta():
    timing = story_producer.build_story_timing(3.0, 58.0, 4.0, padding=0.15)
    assert timing == {
        "intro_duration": 3.15,
        "body_start": 3.15,
        "cta_start": 61.15,
        "cta_end": 65.15,
        "total_duration": 65.15,
    }


def test_story_timing_rejects_total_over_75_seconds():
    with pytest.raises(RuntimeError, match="75"):
        story_producer.build_story_timing(4.0, 68.0, 4.0)


def test_cta_timing_rejects_final_video_over_75_seconds():
    with pytest.raises(RuntimeError, match="75초 초과"):
        story_producer.build_cta_timing(73.0, 3.0)


def test_cta_timing_rejects_final_video_under_60_seconds():
    with pytest.raises(RuntimeError, match="60초 미만"):
        story_producer.build_cta_timing(55.0, 3.0)


def test_story_srt_appends_cta_for_exact_audio_window(tmp_path):
    script = {"scenes": [{"n": 1, "narration": "본문입니다.", "duration_sec": 6}]}
    output = tmp_path / "subs.srt"

    story_producer._write_srt(
        script,
        scene_durations={1: 6.0},
        audio_durations={1: 5.5},
        output=output,
        cta={"text": "구독과 좋아요 부탁드립니다.", "start": 6.0, "end": 9.2},
    )

    subtitles = output.read_text(encoding="utf-8")
    assert "00:00:06,000" in subtitles
    assert "00:00:09,200" in subtitles
    assert "구독" in subtitles
    assert "좋아요" in subtitles


def test_long_cta_is_split_into_short_caption_cues(tmp_path):
    output = tmp_path / "subs.srt"
    story_producer._write_srt(
        {"scenes": []},
        scene_durations={},
        audio_durations={},
        output=output,
        cta={
            "text": "사막의 신비로운 자연 이야기를 계속 보고 싶으시다면 구독과 좋아요 부탁드립니다!",
            "start": 60.0,
            "end": 66.0,
        },
    )

    subtitles = output.read_text(encoding="utf-8")
    assert subtitles.count("-->") >= 2
    assert "00:01:00,000" in subtitles
    assert "00:01:06,000" in subtitles


def test_story_srt_starts_with_title_and_shifts_body(tmp_path):
    script = {"title": "Spoken story title", "scenes": [{
        "n": 1, "narration": "Body narration", "duration_sec": 6,
    }]}
    output = tmp_path / "subs.srt"

    story_producer._write_srt(
        script,
        scene_durations={1: 6.0},
        audio_durations={1: 5.5},
        output=output,
        intro={"text": script["title"], "audio_end": 3.0, "body_start": 3.15},
    )

    subtitles = output.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:03,000" in subtitles
    assert "Spoken story title" in subtitles
    assert "00:00:03,150" in subtitles


def test_cta_visual_filter_adds_dark_overlay():
    vf = story_producer.visual_filter("shot.mp4", duration=3.2, darken=True)
    assert "drawbox" in vf
    assert "black@0.35" in vf


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
