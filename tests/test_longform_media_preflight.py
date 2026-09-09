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


def test_materialize_media_board_downloads_video_before_static_image(
    tmp_path, monkeypatch
):
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
                                "media_type": "image",
                                "download_url": "https://upload.wikimedia.org/richat.jpg",
                            },
                            {
                                "tier": "B",
                                "provider": "pexels_video",
                                "media_type": "video",
                                "download_url": "https://videos.pexels.com/glacier.mp4",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_download(candidate, output):
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"video" * 512)
        return output.stat().st_size

    monkeypatch.setattr(
        "app.services.longform_media_preflight._download_candidate",
        fake_download,
    )

    board = materialize_longform_media_board(tmp_path, "longform-demo")

    materialized = [
        asset for asset in board["scenes"][0]["assets"] if asset.get("local_path")
    ]
    assert materialized[0]["media_type"] == "video"
    assert materialized[0]["local_path"].endswith(".mp4")
    assert Path(materialized[0]["local_path"]).is_file()


def test_preflight_prefers_unused_source_when_same_query_repeats(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)
    script = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
    script["scenes"].append(
        {
            "n": 3,
            "role": "evidence",
            "chapter_title": "반복 장면",
            "narration": "같은 대상도 다른 각도의 자료를 먼저 확인해야 합니다.",
            "duration_sec": 25,
            "visual_query": "exact: Richat Structure",
        }
    )
    (run_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )

    def fake_wikimedia(query):
        if query != "Richat Structure":
            return []
        return [
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat_A.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat_A.jpg",
                download_url="https://upload.wikimedia.org/richat-a.jpg",
                width=1600,
                height=1000,
                media_type="image",
                keyword=query,
                license="CC BY-SA 4.0",
                description="Richat Structure",
            ),
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat_B.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat_B.jpg",
                download_url="https://upload.wikimedia.org/richat-b.jpg",
                width=1600,
                height=1000,
                media_type="image",
                keyword=query,
                license="CC BY-SA 4.0",
                description="Richat Structure",
            ),
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

    first = board["scenes"][0]["assets"][0]["source_url"]
    repeated = board["scenes"][2]["assets"][0]["source_url"]
    assert first.endswith("Richat_A.jpg")
    assert repeated.endswith("Richat_B.jpg")


def test_preflight_orders_video_before_static_image_for_longform_scene(
    tmp_path, monkeypatch
):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        lambda query: [
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat.jpg",
                download_url="https://upload.wikimedia.org/richat.jpg",
                width=1600,
                height=1000,
                media_type="image",
                keyword=query,
                license="CC BY-SA 4.0",
                description="Richat Structure",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._nasa_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_video_candidates",
        lambda query: [
            MediaCandidate(
                provider="pexels_video",
                media_id="pexels-1",
                source_url="https://www.pexels.com/video/1",
                download_url="https://videos.pexels.com/1.mp4",
                width=1920,
                height=1080,
                media_type="video",
                keyword=query,
                license="Pexels",
                description="Richat Structure desert aerial",
            )
        ],
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

    assert board["scenes"][0]["assets"][0]["media_type"] == "video"


def test_preflight_checks_pixabay_when_pexels_only_has_portrait_video(
    tmp_path, monkeypatch
):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._nasa_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_video_candidates",
        lambda query: [
            MediaCandidate(
                provider="pexels_video",
                media_id="pexels-portrait",
                source_url="https://www.pexels.com/video/portrait",
                download_url="https://videos.pexels.com/portrait.mp4",
                width=1080,
                height=1920,
                media_type="video",
                keyword=query,
                license="Pexels",
                description="portrait Richat Structure clip",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pixabay_video_candidates",
        lambda query: [
            MediaCandidate(
                provider="pixabay_video",
                media_id="pixabay-landscape",
                source_url="https://pixabay.com/videos/id-landscape/",
                download_url="https://cdn.pixabay.com/landscape.mp4",
                width=1920,
                height=1080,
                media_type="video",
                keyword=query,
                license="Pixabay",
                description="landscape Richat Structure clip",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_photo_candidates",
        lambda query: [],
    )

    board = prepare_longform_media_board(tmp_path, "longform-demo")

    first = board["scenes"][0]["assets"][0]
    assert first["provider"] == "pixabay_video"
    assert first["width"] > first["height"]


def test_preflight_orders_landscape_unused_video_before_portrait_duplicate():
    from app.services.longform_media_preflight import _asset_sort_key

    portrait_duplicate = {
        "provider": "pexels_video",
        "media_type": "video",
        "width": 1080,
        "height": 1920,
        "duplicate_source": True,
        "tier": "B",
    }
    landscape_unused = {
        "provider": "pexels_video",
        "media_type": "video",
        "width": 1920,
        "height": 1080,
        "duplicate_source": False,
        "tier": "B",
    }

    ordered = sorted([portrait_duplicate, landscape_unused], key=_asset_sort_key)

    assert ordered[0] is landscape_unused


def test_preflight_orders_landscape_duplicate_video_before_portrait_unused():
    from app.services.longform_media_preflight import _asset_sort_key

    portrait_unused = {
        "provider": "pexels_video",
        "media_type": "video",
        "width": 1080,
        "height": 1920,
        "duplicate_source": False,
        "tier": "B",
    }
    landscape_duplicate = {
        "provider": "pexels_video",
        "media_type": "video",
        "width": 1920,
        "height": 1080,
        "duplicate_source": True,
        "tier": "B",
    }

    ordered = sorted([portrait_unused, landscape_duplicate], key=_asset_sort_key)

    assert ordered[0] is landscape_duplicate


def test_preflight_orders_static_image_before_portrait_video_for_longform():
    from app.services.longform_media_preflight import _asset_sort_key

    portrait_video = {
        "provider": "pexels_video",
        "media_type": "video",
        "width": 1080,
        "height": 1920,
        "tier": "B",
    }
    reference_image = {
        "provider": "wikimedia_image",
        "media_type": "image",
        "width": 1920,
        "height": 1080,
        "tier": "A",
    }

    ordered = sorted([portrait_video, reference_image], key=_asset_sort_key)

    assert ordered[0] is reference_image


def test_preflight_uses_scene_visuals_before_chapter_title(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    _write_script(run_dir)
    script_path = run_dir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["scenes"][0].pop("visual_query", None)
    script["scenes"][0]["chapter_title"] = "남극의 붉은 폭포"
    script["scenes"][0]["visuals"] = ["antarctic glacier red waterfall"]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    captured = []

    def fake_video_candidates(query):
        captured.append(query)
        return []

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._nasa_image_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        "app.services.longform_media_preflight._pexels_video_candidates",
        fake_video_candidates,
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

    assert captured[0] == "antarctic glacier red waterfall"
