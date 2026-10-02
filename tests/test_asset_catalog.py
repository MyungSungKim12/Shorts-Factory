import json
from pathlib import Path

from app.services.ai_opening_library import AiOpeningLibrary
from app.services.asset_catalog import (
    rank_asset_backed_candidates,
    topic_matches_asset_candidate,
)


def _write_ready_asset(data_dir: Path, subject: str, asset_id: str) -> None:
    asset_dir = data_dir / "media" / "ai_openings" / asset_id
    asset_dir.mkdir(parents=True)
    for name in ("reference.jpg", "master.mp4", "opening.mp4"):
        (asset_dir / name).write_bytes(b"ready")
    AiOpeningLibrary(data_dir).register_asset(metadata={
        "asset_id": asset_id,
        "subject_key": subject,
        "reuse_scope": "exact_subject",
        "status": "ready",
        "reference_path": str(asset_dir / "reference.jpg"),
        "master_path": str(asset_dir / "master.mp4"),
        "opening_path": str(asset_dir / "opening.mp4"),
        "source_url": f"https://commons.wikimedia.org/wiki/File:{subject}.jpg",
        "license": "CC BY-SA 4.0",
        "source_metadata": {"exact_match": True},
        "model": "veo-test",
        "prompt": "identity preserving motion",
        "validation": {"passed": True},
    })


def _write_batch_state(data_dir: Path) -> None:
    bank = data_dir / "media" / "ai_asset_bank"
    bank.mkdir(parents=True)
    jobs = {}
    subjects = [
        ("strong-place", "강한 지하 장소", "Strong Place Underground", "지하시설", 2),
        ("weaker-place", "평범한 암석 지형", "Weaker Rock Formation", "지형", 1),
        ("image-only", "영상 없는 장소", "Image Only Place", "유적", 0),
    ]
    for key, title, query, category, video_count in subjects:
        subject = {
            "key": key,
            "title_ko": title,
            "exact_query": query,
            "category": category,
        }
        image_dir = bank / key
        image_dir.mkdir(parents=True)
        image = image_dir / "ai-image-vertical.jpg"
        image.write_bytes(b"\xff\xd8" + b"x" * 2048)
        jobs[f"{key}:image:1"] = {
            "status": "completed",
            "job": {"kind": "image", "subject": subject},
            "detail": {"path": str(image)},
        }
        for index in range(video_count):
            jobs[f"{key}:video:{index + 1}"] = {
                "status": "completed",
                "job": {"kind": "video", "subject": subject},
                "detail": {"asset_id": f"{key}-{index + 1}"},
            }
    (bank / "batch_state.json").write_text(
        json.dumps({"version": 1, "jobs": jobs}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_catalog_only_returns_assets_with_real_ready_video_files(tmp_path):
    _write_batch_state(tmp_path)
    _write_ready_asset(tmp_path, "strong-place-underground", "strong-place-1")
    _write_ready_asset(tmp_path, "strong-place-underground", "strong-place-2")
    _write_ready_asset(tmp_path, "weaker-rock-formation", "weaker-place-1")

    candidates = rank_asset_backed_candidates(tmp_path, limit=10)

    assert [item["subject_key"] for item in candidates] == [
        "strong-place-underground",
        "weaker-rock-formation",
    ]
    assert candidates[0]["ready_video_count"] == 2
    assert candidates[0]["has_ai_image"] is True
    assert all(item["production_ready"] for item in candidates)


def test_catalog_excludes_a_subject_already_present_in_upload_history(tmp_path):
    _write_batch_state(tmp_path)
    _write_ready_asset(tmp_path, "strong-place-underground", "strong-place-1")
    _write_ready_asset(tmp_path, "weaker-rock-formation", "weaker-place-1")

    candidates = rank_asset_backed_candidates(
        tmp_path,
        avoid_subjects=["강한 지하 장소에 숨겨진 시설의 기록"],
        limit=10,
    )

    assert [item["subject_key"] for item in candidates] == [
        "weaker-rock-formation"
    ]


def test_catalog_excludes_same_place_when_prior_title_uses_an_alias(tmp_path):
    bank = tmp_path / "media" / "ai_asset_bank"
    bank.mkdir(parents=True)
    subject = {
        "key": "duga-radar",
        "title_ko": "체르노빌 두가 레이더",
        "exact_query": "Duga radar",
        "category": "폐시설",
    }
    image_dir = bank / "duga-radar"
    image_dir.mkdir()
    (image_dir / "ai-image-vertical.jpg").write_bytes(b"\xff\xd8" + b"x" * 2048)
    (bank / "batch_state.json").write_text(json.dumps({
        "version": 1,
        "jobs": {
            "duga-radar:image:1": {
                "status": "completed",
                "job": {"kind": "image", "subject": subject},
            },
            "duga-radar:video:1": {
                "status": "completed",
                "job": {"kind": "video", "subject": subject},
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    _write_ready_asset(tmp_path, "duga-radar", "duga-radar-1")

    candidates = rank_asset_backed_candidates(
        tmp_path,
        avoid_subjects=["체르노빌 숲속 150m, 소련의 거대 딱따구리 레이더"],
    )

    assert candidates == []


def test_catalog_normalizes_korean_particles_when_checking_duplicate_aliases(tmp_path):
    bank = tmp_path / "media" / "ai_asset_bank"
    subject = {
        "key": "paris-catacombs",
        "title_ko": "파리 카타콤",
        "exact_query": "Catacombs of Paris",
        "category": "지하유적",
    }
    image_dir = bank / "paris-catacombs"
    image_dir.mkdir(parents=True)
    (image_dir / "ai-image-vertical.jpg").write_bytes(b"\xff\xd8" + b"x" * 2048)
    (bank / "batch_state.json").write_text(json.dumps({
        "version": 1,
        "jobs": {
            "paris-catacombs:image:1": {
                "status": "completed",
                "job": {"kind": "image", "subject": subject},
            },
            "paris-catacombs:video:1": {
                "status": "completed",
                "job": {"kind": "video", "subject": subject},
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    _write_ready_asset(tmp_path, "catacombs-of-paris", "paris-catacombs-1")

    candidates = rank_asset_backed_candidates(
        tmp_path,
        avoid_subjects=["파리 지하 600만 해골, 카타콤의 충격적인 비밀"],
    )

    assert candidates == []


def test_catalog_excludes_three_character_korean_place_name_from_history(tmp_path):
    bank = tmp_path / "media" / "ai_asset_bank"
    subject = {
        "key": "nazca-lines",
        "title_ko": "나스카 지상화",
        "exact_query": "Nazca Lines",
        "category": "유적",
    }
    image_dir = bank / "nazca-lines"
    image_dir.mkdir(parents=True)
    (image_dir / "ai-image-vertical.jpg").write_bytes(b"\xff\xd8" + b"x" * 2048)
    (bank / "batch_state.json").write_text(json.dumps({
        "version": 1,
        "jobs": {
            "nazca-lines:image:1": {
                "status": "completed",
                "job": {"kind": "image", "subject": subject},
            },
            "nazca-lines:video:1": {
                "status": "completed",
                "job": {"kind": "video", "subject": subject},
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    _write_ready_asset(tmp_path, "nazca-lines", "nazca-lines-1")

    candidates = rank_asset_backed_candidates(
        tmp_path,
        avoid_subjects=["페루 나스카, 하늘에서만 보이는 2천 년 그림"],
    )

    assert candidates == []


def test_selected_topic_must_use_one_of_the_ranked_asset_subjects():
    candidates = [{
        "subject_key": "strong-place-underground",
        "title_ko": "강한 지하 장소",
        "exact_query": "Strong Place Underground",
    }]

    matching = {
        "topic": "강한 지하 장소의 숨겨진 시설",
        "target_keyword": "Strong Place Underground",
        "visual_identity": {
            "exact_queries": ["exact:Strong Place Underground"],
            "safe_fallbacks": ["underground stone chamber"],
            "required_exact": True,
        },
    }
    unrelated = {
        **matching,
        "visual_identity": {
            **matching["visual_identity"],
            "exact_queries": ["exact:Unrelated Place"],
        },
    }

    assert topic_matches_asset_candidate(matching, candidates) is True
    assert topic_matches_asset_candidate(unrelated, candidates) is False
