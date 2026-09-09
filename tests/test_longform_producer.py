import json
from pathlib import Path


def _script():
    value = {
        "format": "longform",
        "title": "사막 아래 사라진 도시의 흔적",
        "description": "사막 유적의 실제 기록을 따라가는 미스터리 다큐멘터리입니다.",
        "tags": ["사막", "고대도시", "미스터리"],
        "hook": "위성사진 속 직선은 왜 사막 한가운데 남았을까요?",
        "scenes": [
            {
                "n": 1,
                "role": "hook",
                "chapter_title": "사라진 도시의 첫 단서",
                "narration": "오늘의 주제는 사막 아래 사라진 도시의 흔적입니다. 첫 기록은 위성사진의 이상한 직선에서 시작됩니다.",
                "visuals": ["ancient desert ruin satellite image"],
                "duration_sec": 50,
            },
            {
                "n": 2,
                "role": "context",
                "chapter_title": "왜 이상하게 보였나",
                "narration": "주변 지형과 달리 이 선은 일정한 각도로 이어졌습니다.",
                "visuals": ["desert plateau aerial"],
                "duration_sec": 55,
            },
            {
                "n": 3,
                "role": "evidence",
                "chapter_title": "남은 흔적",
                "narration": "조사 기록에는 흙벽과 물길의 흔적이 함께 남았습니다.",
                "visuals": ["ancient canal remains"],
                "duration_sec": 55,
            },
            {
                "n": 4,
                "role": "mechanism",
                "chapter_title": "가능한 설명",
                "narration": "가장 조심스러운 해석은 방어와 물 관리가 결합된 구조입니다.",
                "visuals": ["ancient irrigation diagram"],
                "duration_sec": 60,
            },
            {
                "n": 5,
                "role": "counterpoint",
                "chapter_title": "아직 풀리지 않은 부분",
                "narration": "하지만 모든 선이 같은 시기에 만들어졌다는 증거는 부족합니다.",
                "visuals": ["archaeologist field notes"],
                "duration_sec": 50,
            },
            {
                "n": 6,
                "role": "payoff",
                "chapter_title": "기록이 말하는 것",
                "narration": "이 유적은 사라진 도시가 환경을 어떻게 읽었는지 보여줍니다.",
                "visuals": ["ruined city sunset"],
                "duration_sec": 50,
            },
            {
                "n": 7,
                "role": "close",
                "chapter_title": "다음 질문",
                "narration": "남은 질문은 이 구조가 어디까지 이어졌는지입니다.",
                "visuals": ["aerial desert mystery"],
                "duration_sec": 40,
            },
        ],
        "cta": "이런 지구의 기록이 더 궁금하다면 구독과 좋아요 부탁드립니다.",
    }
    templates = value["scenes"][:-1]
    close = value["scenes"][-1]
    roles = [
        "hook", "context", "evidence", "mechanism", "evidence",
        "counterpoint", "mechanism", "payoff", "evidence", "context",
        "mechanism", "counterpoint", "evidence", "payoff", "mechanism",
        "context", "evidence", "counterpoint", "payoff", "close",
    ] * 2
    roles[-1] = "close"
    value["scenes"] = []
    for index, role in enumerate(roles, start=1):
        template = close if role == "close" else templates[(index - 1) % len(templates)]
        scene = dict(template)
        scene.update(
            n=index,
            role=role,
            chapter_title=f"{template['chapter_title']} {index}",
            duration_sec=12,
        )
        value["scenes"].append(scene)
    return value


def test_longform_producer_writes_output_and_log_without_touching_shorts_work(
    tmp_path, monkeypatch
):
    from app.agents import longform_producer

    run_id = "longform-demo"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    commands = []

    def fake_prepare(text, raw, wav, ffmpeg_path, ssml=None):
        raw.write_bytes(b"mp3")
        wav.write_bytes(b"wav")
        return type("R", (), {"provider": "google", "voice": "Kore", "speaking_rate": 1.0})(), 9.0

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        Path(command[-1]).write_bytes(b"media")

    monkeypatch.setenv("TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_REQUIRE_VIDEO_SOURCES", "0")
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["format"] == "longform"
    assert Path(result["output_file"]).read_bytes() == b"media"
    assert Path(result["thumbnail_file"]).is_file()
    assert (work_dir / "produce_log.json").is_file()
    assert not (tmp_path / "work" / run_id).exists()
    assert commands


def test_longform_producer_reuses_permanent_ai_asset(tmp_path, monkeypatch):
    from app.agents import longform_producer

    run_id = "longform-ai"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    script = _script()
    script["visual_identity"] = {
        "required_exact": True,
        "exact_queries": ["exact: Richat Structure"],
        "safe_fallbacks": ["desert aerial"],
    }
    (work_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    ai_dir = tmp_path / "media" / "ai_openings" / "asset-1"
    ai_dir.mkdir(parents=True)
    reference = ai_dir / "reference.jpg"
    master = ai_dir / "master.mp4"
    opening = ai_dir / "opening.mp4"
    for path in (reference, master, opening):
        path.write_bytes(b"ai")
    from app.services.ai_opening_library import AiOpeningLibrary

    library = AiOpeningLibrary(tmp_path)
    library.register_asset(metadata={
        "asset_id": "asset-1",
        "subject_key": "richat-structure",
        "reuse_scope": "exact_subject",
        "status": "ready",
        "reference_path": str(reference),
        "master_path": str(master),
        "opening_path": str(opening),
        "source_url": "https://example.com/richat",
        "license": "test",
        "source_metadata": {"provider": "wikimedia_image", "media_id": "File:Richat.jpg"},
        "model": "veo-3.1-fast-generate-001",
        "prompt": "identity preserving",
    })

    def fake_prepare(text, raw, wav, ffmpeg_path, ssml=None):
        raw.write_bytes(b"mp3")
        wav.write_bytes(b"wav")
        return type("R", (), {"provider": "google", "voice": "Kore", "speaking_rate": 1.0})(), 9.0

    monkeypatch.setenv("TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_REQUIRE_VIDEO_SOURCES", "0")
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(
        longform_producer,
        "_run_ffmpeg",
        lambda command, cwd=None, timeout=None: Path(command[-1]).write_bytes(b"media"),
    )

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["ai_assets"][0]["asset_id"] == "asset-1"
    assert result["ai_assets"][0]["reused"] is True
    assert json.loads((work_dir / "produce_log.json").read_text(encoding="utf-8"))[
        "ai_assets"
    ][0]["reused"] is True


def test_longform_producer_records_media_board_usage(tmp_path, monkeypatch):
    from app.agents import longform_producer

    run_id = "longform-media-board"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    (work_dir / "media_board.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "gate": {
                    "passed": True,
                    "quality_runtime_ratio": 0.84,
                    "reasons": [],
                },
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "assets": [{"tier": "A", "provider": "wikimedia_image"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_prepare(text, raw, wav, ffmpeg_path, ssml=None):
        raw.write_bytes(b"mp3")
        wav.write_bytes(b"wav")
        return type("R", (), {"provider": "google", "voice": "Kore", "speaking_rate": 1.0})(), 9.0

    monkeypatch.setenv("TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_REQUIRE_VIDEO_SOURCES", "0")
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(
        longform_producer,
        "_run_ffmpeg",
        lambda command, cwd=None, timeout=None: Path(command[-1]).write_bytes(b"media"),
    )

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["media_board_used"] is True
    assert result["media_quality_gate"]["passed"] is True
    assert result["media_quality_gate"]["quality_runtime_ratio"] == 0.84


def test_longform_producer_uses_materialized_media_from_board(tmp_path, monkeypatch):
    from app.agents import longform_producer

    run_id = "longform-materialized"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    script = _script()
    (work_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    media = work_dir / "media" / "scene-01-01.jpg"
    media.parent.mkdir()
    media.write_bytes(b"\xff\xd8" + b"x" * 2048)
    (work_dir / "media_board.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "gate": {"passed": True, "quality_runtime_ratio": 1.0, "reasons": []},
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "assets": [
                            {
                                "tier": "A",
                                "provider": "wikimedia_image",
                                "local_path": media.as_posix(),
                                "source_url": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    commands = []

    def fake_prepare(text, raw, wav, ffmpeg_path, ssml=None):
        raw.write_bytes(b"mp3")
        wav.write_bytes(b"wav")
        return type("R", (), {"provider": "google", "voice": "Kore", "speaking_rate": 1.0})(), 9.0

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        Path(command[-1]).write_bytes(b"media")

    monkeypatch.setenv("TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.0")
    monkeypatch.setenv("LONGFORM_REQUIRE_VIDEO_SOURCES", "0")
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["media_sources"][0]["local_path"] == media.as_posix()
    assert any(str(media) in command for command in commands for command in command)


def test_longform_preview_rejects_static_image_media_board(tmp_path):
    from app.agents import longform_producer

    run_id = "longform-preview-static"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    image = work_dir / "media" / "scene-01-01.jpg"
    image.parent.mkdir()
    image.write_bytes(b"jpg")
    (work_dir / "media_board.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "assets": [
                            {
                                "tier": "C",
                                "provider": "preview_reference",
                                "media_type": "image",
                                "local_path": image.as_posix(),
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        longform_producer.run_longform_preview(tmp_path, run_id, "ffmpeg")
    except ValueError as exc:
        assert "영상 소스" in str(exc)
    else:
        raise AssertionError("preview should reject static image-only media")


def test_longform_final_render_rejects_missing_video_source(tmp_path):
    from app.agents import longform_producer

    run_id = "longform-final-static"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    (work_dir / "media_board.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "assets": [
                            {
                                "tier": "B",
                                "provider": "pexels_image",
                                "media_type": "image",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")
    except ValueError as exc:
        assert "미디어가 없는 장면" in str(exc)
    else:
        raise AssertionError("final longform render should require video sources")


def test_longform_style_previews_create_selectable_pngs(tmp_path):
    from app.agents.longform_producer import generate_longform_style_previews

    result = generate_longform_style_previews(
        tmp_path,
        title="사막 아래 사라진 도시의 흔적",
        chapter_title="첫 번째 단서",
        caption="위성사진 속 직선은 왜 사막 한가운데 남았을까요?",
    )

    assert [item["style_id"] for item in result["styles"]] == [
        "documentary",
        "cinematic",
        "clean_news",
    ]
    for item in result["styles"]:
        assert Path(item["preview_file"]).is_file()
        assert item["subtitle_font_size"] >= 24


def test_create_longform_thumbnail_writes_clickable_banner(tmp_path):
    from app.agents.longform_producer import create_longform_thumbnail
    from PIL import Image

    script = _script()
    script["thumbnail_main"] = "지구가 숨긴 TOP 5"
    script["thumbnail_sub"] = "이건 진짜 이상함"
    output = tmp_path / "thumbnail.png"

    result = create_longform_thumbnail(script, output)

    assert Path(result["thumbnail_file"]).is_file()
    assert result["main_text"] == "지구가 숨긴 TOP 5"
    assert result["sub_text"] == "이건 진짜 이상함"
    with Image.open(output) as image:
        assert image.size == (1280, 720)


def test_thumbnail_main_text_splits_into_two_impact_lines():
    from app.agents.longform_producer import _thumbnail_main_lines

    assert _thumbnail_main_lines("남극의 피폭포") == ["남극의", "피폭포"]
    assert _thumbnail_main_lines("지구가 숨긴 TOP 5") == ["지구가 숨긴", "TOP 5"]


def test_clean_news_subtitle_style_is_not_shorts_caption_style():
    from app.agents.longform_producer import _longform_subtitle_style

    style = _longform_subtitle_style("NanumGothic", "clean_news")

    assert "FontName=NanumGothic" in style
    assert "FontSize=15" in style
    assert "Outline=1" in style
    assert "Outline=3" not in style
    assert "Shadow=0" in style
    assert "BorderStyle=4" in style
    assert "BackColour=&H80000000" in style
    assert "MarginL=320" in style
    assert "MarginR=320" in style
    assert "MarginV=58" in style


def test_longform_srt_wraps_long_lines_to_two_safe_lines(tmp_path):
    from app.agents.longform_producer import _write_longform_srt

    script = {
        "scenes": [
            {
                "n": 1,
                "narration": (
                    "남극 빙하 아래에서 붉은 물이 흘러나오는 장면은 처음 보면 합성처럼 보이지만 "
                    "실제로는 철 성분과 소금물이 만든 자연 현상입니다."
                ),
            }
        ]
    }
    output = tmp_path / "longform.srt"

    _write_longform_srt(script, {1: 0.0}, {1: 8.0}, output)

    text_lines = [
        line for line in output.read_text(encoding="utf-8").splitlines()
        if line and "-->" not in line and not line.isdigit()
    ]
    assert text_lines
    assert all(len(line) <= 40 for line in text_lines)
    assert any("\n" in block for block in output.read_text(encoding="utf-8").split("\n\n"))


def test_longform_playback_tempo_defaults_to_shorts_like_speed(monkeypatch):
    from app.agents.longform_producer import _longform_playback_tempo

    monkeypatch.setenv("TTS_SPEED", "1.2")
    monkeypatch.delenv("LONGFORM_TTS_SPEED", raising=False)

    assert _longform_playback_tempo() == 1.2


def test_longform_playback_tempo_uses_dedicated_setting(monkeypatch):
    from app.agents.longform_producer import _longform_playback_tempo

    monkeypatch.setenv("TTS_SPEED", "1.2")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.1")

    assert _longform_playback_tempo() == 1.1


def test_longform_scene_duration_matches_audio_without_dead_air():
    from app.agents.longform_producer import _longform_scene_duration

    assert _longform_scene_duration(18.0, 9.0) == 9.0
    assert _longform_scene_duration(8.0, 9.0) == 9.0


def test_longform_still_filter_keeps_images_static():
    from app.agents.longform_producer import _longform_still_filter

    result = _longform_still_filter()

    assert "zoompan" not in result
    assert "scale=1920:1080" in result
    assert "crop=1920:1080" in result
    assert "fps=30" in result


def test_longform_video_filter_fills_landscape_frame_without_blurred_sidebars():
    from app.agents.longform_producer import _longform_video_filter

    result = _longform_video_filter()

    assert "force_original_aspect_ratio=increase" in result
    assert "crop=1920:1080" in result
    assert "overlay=" not in result
    assert "gblur" not in result


def test_longform_final_render_rejects_portrait_video_source(tmp_path):
    from app.agents import longform_producer

    script = _script()
    videos = []
    for index, _scene in enumerate(script["scenes"], start=1):
        video = tmp_path / f"scene-{index:02d}.mp4"
        video.write_bytes(b"mp4")
        videos.append(video)
    media_board = {
        "scenes": [
            {
                "n": scene["n"],
                "assets": [
                    {
                        "provider": "pexels_video",
                        "media_type": "video",
                        "local_path": videos[index - 1].as_posix(),
                        "width": 1920,
                        "height": 1080,
                    }
                ],
            }
            for index, scene in enumerate(script["scenes"], start=1)
        ]
    }
    media_board["scenes"][0]["assets"][0]["width"] = 1080
    media_board["scenes"][0]["assets"][0]["height"] = 1920

    try:
        longform_producer._assert_final_media_mix(script, media_board)
    except ValueError as exc:
        assert "세로 영상" in str(exc)
    else:
        raise AssertionError("portrait source should be rejected for longform")


def test_longform_card_does_not_pad_audio_with_silence(tmp_path, monkeypatch):
    from app.agents import longform_producer

    commands = []
    image = tmp_path / "image.jpg"
    narration = tmp_path / "narration.wav"
    output = tmp_path / "scene.mp4"
    image.write_bytes(b"jpg")
    narration.write_bytes(b"wav")

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        Path(command[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    longform_producer._encode_longform_card(
        image,
        narration,
        output,
        60.0,
        "ffmpeg",
        motion_index=1,
    )

    command = commands[0]
    assert "apad" not in command


def test_longform_video_does_not_pad_audio_with_silence(tmp_path, monkeypatch):
    from app.agents import longform_producer

    commands = []
    media = tmp_path / "media.mp4"
    narration = tmp_path / "narration.wav"
    output = tmp_path / "scene.mp4"
    media.write_bytes(b"mp4")
    narration.write_bytes(b"wav")

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        Path(command[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    longform_producer._encode_longform_media(
        media,
        narration,
        output,
        60.0,
        "ffmpeg",
        motion_index=1,
    )

    command = commands[0]
    assert "apad" not in command


def test_longform_concat_reencodes_timestamps_for_smooth_scene_boundaries(
    tmp_path, monkeypatch
):
    from app.agents import longform_producer

    commands = []
    files = []
    for index in range(2):
        scene = tmp_path / f"scene-{index}.mp4"
        scene.write_bytes(b"mp4")
        files.append(scene)
    output = tmp_path / "concat.mp4"

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        output.write_bytes(b"mp4")

    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    longform_producer._concat_longform_files(files, output, "ffmpeg", tmp_path)

    command = commands[0]
    assert "-c" not in command
    assert "libx264" in command
    assert "aac" in command
    assert any("setpts=PTS-STARTPTS" in item for item in command)
    assert any("aresample=async=1:first_pts=0" in item for item in command)


def test_finish_longform_caps_output_to_planned_duration(tmp_path, monkeypatch):
    from app.agents import longform_producer

    commands = []
    concat_video = tmp_path / "concat.mp4"
    subtitles = tmp_path / "longform.srt"
    output = tmp_path / "output.mp4"
    concat_video.write_bytes(b"mp4")
    subtitles.write_text("", encoding="utf-8")

    def fake_run(command, cwd=None, timeout=None):
        commands.append(command)
        output.write_bytes(b"mp4")

    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    longform_producer._finish_longform(
        concat_video,
        output,
        subtitles,
        "ffmpeg",
        tmp_path,
        planned_duration=360.0,
    )

    command = commands[0]
    assert "-t" in command
    assert command[command.index("-t") + 1] == "360.000"
