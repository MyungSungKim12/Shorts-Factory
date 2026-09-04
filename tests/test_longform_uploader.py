import json
from pathlib import Path


def _script():
    return {
        "format": "longform",
        "title": "남극 블러드 폴스, 붉은 빙하의 기록",
        "description": "남극 블러드 폴스의 실제 기록을 따라가는 미스터리 다큐멘터리입니다.",
        "tags": ["블러드폴스", "남극", "자연미스터리"],
        "hook": "빙하에서 붉은 물이 흐른다는 기록은 왜 오래 남았을까요?",
        "style_id": "clean_news",
        "scenes": [
            {
                "n": 1,
                "role": "hook",
                "chapter_title": "붉은 빙하",
                "narration": "남극 빙하 아래에서 붉은 물이 흘러나옵니다.",
                "visuals": ["Blood Falls Antarctica"],
                "duration_sec": 40,
            },
            {
                "n": 2,
                "role": "context",
                "chapter_title": "테일러 빙하",
                "narration": "이 현상은 테일러 빙하 끝에서 관측됩니다.",
                "visuals": ["Taylor Glacier Antarctica"],
                "duration_sec": 40,
            },
            {
                "n": 3,
                "role": "evidence",
                "chapter_title": "철 성분",
                "narration": "붉은 색은 철 성분이 산화되며 나타나는 것으로 설명됩니다.",
                "visuals": ["iron oxide water"],
                "duration_sec": 40,
            },
            {
                "n": 4,
                "role": "mechanism",
                "chapter_title": "소금물 저장고",
                "narration": "빙하 아래에는 오래 갇힌 소금물 저장고가 있습니다.",
                "visuals": ["subglacial brine"],
                "duration_sec": 40,
            },
            {
                "n": 5,
                "role": "payoff",
                "chapter_title": "극한의 생명",
                "narration": "이곳은 극한 환경 생명 연구에도 단서를 남깁니다.",
                "visuals": ["Antarctica research"],
                "duration_sec": 40,
            },
            {
                "n": 6,
                "role": "close",
                "chapter_title": "남은 질문",
                "narration": "남은 질문은 이 물길이 얼마나 오래 이어졌는지입니다.",
                "visuals": ["Antarctica glacier aerial"],
                "duration_sec": 40,
            },
        ],
        "cta": "이런 지구의 기록이 더 궁금하다면 구독과 좋아요 부탁드립니다.",
    }


def test_longform_uploader_uploads_output_and_writes_log(tmp_path, monkeypatch):
    from app.agents import longform_uploader

    run_id = "longform-demo"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    (work_dir / "produce_log.json").write_text(
        json.dumps({"media_sources": []}, ensure_ascii=False), encoding="utf-8"
    )
    (work_dir / "output.mp4").write_bytes(b"mp4")
    seen = {}

    class Request:
        def next_chunk(self):
            return None, {"id": "abc123"}

    class Videos:
        def insert(self, part, body, media_body):
            seen.update(part=part, body=body, media_body=media_body)
            return Request()

    class Youtube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(longform_uploader, "_get_youtube_client", lambda: Youtube())
    monkeypatch.setattr(
        longform_uploader,
        "_validate_longform_upload_package",
        lambda *args, **kwargs: {"passed": True, "report": {"duration": 240}},
    )

    result = longform_uploader.run_longform_uploader(tmp_path, run_id)

    assert result["status"] == "uploaded"
    assert result["url"] == "https://youtube.com/watch?v=abc123"
    assert seen["body"]["snippet"]["title"] == _script()["title"]
    assert "#블러드폴스" in seen["body"]["snippet"]["description"]
    assert json.loads((work_dir / "upload_log.json").read_text(encoding="utf-8"))[
        "video_id"
    ] == "abc123"


def test_longform_uploader_skips_existing_upload_log(tmp_path, monkeypatch):
    from app.agents import longform_uploader

    run_id = "longform-demo"
    work_dir = tmp_path / "longform" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "upload_log.json").write_text(
        json.dumps({"status": "uploaded", "video_id": "old"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        longform_uploader,
        "_get_youtube_client",
        lambda: (_ for _ in ()).throw(AssertionError("should not upload twice")),
    )

    result = longform_uploader.run_longform_uploader(tmp_path, run_id)

    assert result["status"] == "skipped"
    assert result["video_id"] == "old"


def test_validate_longform_upload_package_accepts_landscape_video_probe(
    tmp_path, monkeypatch
):
    from app.agents import longform_uploader

    work_dir = tmp_path / "longform" / "longform-demo"
    work_dir.mkdir(parents=True)
    (work_dir / "script.json").write_text(
        json.dumps(_script(), ensure_ascii=False), encoding="utf-8"
    )
    (work_dir / "produce_log.json").write_text("{}", encoding="utf-8")
    (work_dir / "output.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(
        longform_uploader,
        "_probe_longform_video",
        lambda path, ffprobe: {
            "width": 1920,
            "height": 1080,
            "duration": 300.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "duration_delta": 0.1,
            "internal_silence_max": 0.0,
        },
    )

    result = longform_uploader._validate_longform_upload_package(work_dir, "ffmpeg")

    assert result["passed"] is True
