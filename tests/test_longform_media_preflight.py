import json
from pathlib import Path

from app.services.media_library import MediaCandidate


def _write_script(run_dir):
    run_dir.mkdir(parents=True)
    (run_dir / "script.json").write_text(
        json.dumps(
            {
                "format": "longform",
                "title": "사하라의 눈, 리차트 구조의 비밀",
                "hook": "위성사진에 남은 거대한 고리는 무엇일까요?",
                "total_duration_sec": 240,
                "style_id": "clean_news",
                "tags": ["미스터리", "지구기록"],
                "visual_identity": {
                    "required_exact": True,
                    "exact_queries": ["exact: Richat Structure"],
                },
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "chapter_title": "위성사진의 고리",
                        "narration": "사하라 한가운데에는 거대한 눈처럼 보이는 지형이 있습니다.",
                        "duration_sec": 30,
                        "visual_query": "exact: Richat Structure",
                    },
                    {
                        "n": 2,
                        "role": "context",
                        "chapter_title": "사막의 위치",
                        "narration": "이 지형은 모리타니아 사막 안쪽에 있습니다.",
                        "duration_sec": 25,
                        "visual_query": "Mauritania desert Richat",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_preflight_writes_media_board_and_contact_sheet(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)

    def fake_wikimedia(query):
        return [
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat Structure.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat.jpg",
                download_url="https://upload.wikimedia.org/richat.jpg",
                width=1600,
                height=1000,
                media_type="image",
                keyword=query,
                license="CC BY-SA 4.0",
                description="Richat Structure",
            )
        ]

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        fake_wikimedia,
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._nasa_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_video_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_photo_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pixabay_video_candidates",
        lambda query: [],
    )

    board = prepare_longform_media_board(tmp_path, "longform-demo")

    assert board["run_id"] == "longform-demo"
    assert board["scenes"][0]["assets"][0]["tier"] == "A"
    assert board["gate"]["passed"] is True
    assert (run_dir / "media_board.json").is_file()
    assert (run_dir / "media_contact_sheet.png").is_file()


def test_preflight_strips_exact_prefix_before_provider_search(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)
    seen = []

    def fake_wikimedia(query):
        seen.append(query)
        return [
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat Structure.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat.jpg",
                download_url="https://upload.wikimedia.org/richat.jpg",
                width=1600,
                height=1000,
                media_type="image",
                keyword=query,
                license="CC BY-SA 4.0",
                description="Richat Structure",
            )
        ]

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        fake_wikimedia,
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._nasa_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_video_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_photo_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pixabay_video_candidates",
        lambda query: [],
    )

    prepare_longform_media_board(tmp_path, "longform-demo")

    assert "Richat Structure" in seen
    assert "exact: Richat Structure" not in seen


def test_preflight_records_weak_board_when_core_media_is_missing(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)
    for name in (
        "_wikimedia_image_candidates",
        "_nasa_image_candidates",
        "_pexels_video_candidates",
        "_pexels_photo_candidates",
        "_pixabay_video_candidates",
    ):
        monkeypatch.setattr(
            f"app.services.longform_media_preflight.{name}",
            lambda query: [],
        )

    board = prepare_longform_media_board(tmp_path, "longform-demo")

    assert board["gate"]["passed"] is False
    assert "core scene lacks Tier A/C media" in board["gate"]["reasons"][0]


def test_materialize_media_board_downloads_best_asset(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import materialize_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "media_board.json").write_text(
        json.dumps(
            {
                "run_id": "longform-demo",
                "scenes": [
                    {
                        "n": 1,
                        "role": "hook",
                        "assets": [
                            {
                                "tier": "A",
                                "provider": "wikimedia_image",
                                "media_id": "File:Richat.jpg",
                                "source_url": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
                                "download_url": "https://upload.wikimedia.org/richat.jpg",
                                "width": 1600,
                                "height": 1000,
                                "media_type": "image",
                                "keyword": "exact: Richat Structure",
                                "license": "CC BY-SA 4.0",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_download(candidate, output):
        output.write_bytes(b"\xff\xd8" + b"x" * 2048)
        return output.stat().st_size

    monkeypatch.setattr(
        "app.services.longform_media_preflight._download_candidate",
        fake_download,
    )

    board = materialize_longform_media_board(tmp_path, "longform-demo")

    asset = board["scenes"][0]["assets"][0]
    assert asset["local_path"].endswith("longform-demo/media/scene-01-01.jpg")
    assert asset["download_bytes"] > 1024
    assert Path(asset["local_path"]).is_file()
