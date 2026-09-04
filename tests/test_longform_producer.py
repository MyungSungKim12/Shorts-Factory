import json
from pathlib import Path


def _script():
    return {
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
                "narration": "첫 기록은 위성사진의 이상한 직선에서 시작됩니다.",
                "visuals": ["ancient desert ruin satellite image"],
                "duration_sec": 40,
            },
            {
                "n": 2,
                "role": "context",
                "chapter_title": "왜 이상하게 보였나",
                "narration": "주변 지형과 달리 이 선은 일정한 각도로 이어졌습니다.",
                "visuals": ["desert plateau aerial"],
                "duration_sec": 45,
            },
            {
                "n": 3,
                "role": "evidence",
                "chapter_title": "남은 흔적",
                "narration": "조사 기록에는 흙벽과 물길의 흔적이 함께 남았습니다.",
                "visuals": ["ancient canal remains"],
                "duration_sec": 50,
            },
            {
                "n": 4,
                "role": "mechanism",
                "chapter_title": "가능한 설명",
                "narration": "가장 조심스러운 해석은 방어와 물 관리가 결합된 구조입니다.",
                "visuals": ["ancient irrigation diagram"],
                "duration_sec": 55,
            },
            {
                "n": 5,
                "role": "counterpoint",
                "chapter_title": "아직 풀리지 않은 부분",
                "narration": "하지만 모든 선이 같은 시기에 만들어졌다는 증거는 부족합니다.",
                "visuals": ["archaeologist field notes"],
                "duration_sec": 45,
            },
            {
                "n": 6,
                "role": "payoff",
                "chapter_title": "기록이 말하는 것",
                "narration": "이 유적은 사라진 도시가 환경을 어떻게 읽었는지 보여줍니다.",
                "visuals": ["ruined city sunset"],
                "duration_sec": 45,
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
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["format"] == "longform"
    assert Path(result["output_file"]).read_bytes() == b"media"
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
    monkeypatch.setattr(longform_producer, "_prepare_narration", fake_prepare)
    monkeypatch.setattr(longform_producer, "_duration", lambda path, ffmpeg: 9.0)
    monkeypatch.setattr(longform_producer, "_run_ffmpeg", fake_run)

    result = longform_producer.run_longform_producer(tmp_path, run_id, "ffmpeg")

    assert result["media_sources"][0]["local_path"] == media.as_posix()
    assert any(str(media) in command for command in commands for command in command)


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


def test_clean_news_subtitle_style_is_not_shorts_caption_style():
    from app.agents.longform_producer import _longform_subtitle_style

    style = _longform_subtitle_style("Malgun Gothic", "clean_news")

    assert "FontSize=16" in style
    assert "Outline=1" in style
    assert "Outline=3" not in style
    assert "Shadow=0" in style
    assert "BorderStyle=4" in style
    assert "BackColour=&HCC000000" in style
    assert "MarginV=42" in style


def test_longform_playback_tempo_defaults_to_clear_documentary_speed(monkeypatch):
    from app.agents.longform_producer import _longform_playback_tempo

    monkeypatch.setenv("TTS_SPEED", "1.2")
    monkeypatch.delenv("LONGFORM_TTS_SPEED", raising=False)

    assert _longform_playback_tempo() == 1.0


def test_longform_playback_tempo_uses_dedicated_setting(monkeypatch):
    from app.agents.longform_producer import _longform_playback_tempo

    monkeypatch.setenv("TTS_SPEED", "1.2")
    monkeypatch.setenv("LONGFORM_TTS_SPEED", "1.1")

    assert _longform_playback_tempo() == 1.1


def test_longform_still_filter_keeps_images_static():
    from app.agents.longform_producer import _longform_still_filter

    result = _longform_still_filter()

    assert "zoompan" not in result
    assert "scale=1920:1080" in result
    assert "crop=1920:1080" in result
    assert "fps=30" in result
