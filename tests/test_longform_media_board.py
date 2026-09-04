from app.services.longform_media_board import (
    longform_media_gate,
    media_tier_for_source,
    scene_media_quality,
)


def test_exact_wikimedia_counts_as_tier_a():
    source = {
        "provider": "wikimedia_image",
        "exact_match": True,
        "keyword": "exact: Richat Structure",
        "media_id": "File:Richat Structure.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
    }

    assert media_tier_for_source(source) == "A"


def test_reference_based_ai_asset_counts_as_tier_c():
    source = {
        "provider": "veo",
        "asset_id": "asset-1",
        "source_reference": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
    }

    assert media_tier_for_source(source) == "C"


def test_scene_media_quality_marks_core_scene_without_exact_or_ai_as_weak():
    scene = {"n": 1, "role": "hook", "duration_sec": 12}
    quality = scene_media_quality(
        scene,
        [{"provider": "pexels_video", "strong_match": True, "duration_sec": 12}],
    )

    assert quality["best_tier"] == "B"
    assert quality["core_has_exact_or_ai"] is False


def test_generic_stock_cannot_pass_core_scene_alone():
    board = {
        "run_id": "longform-demo",
        "scenes": [
            {
                "n": 1,
                "role": "hook",
                "duration_sec": 12,
                "assets": [{"tier": "D", "provider": "pexels_video"}],
            },
            {
                "n": 2,
                "role": "evidence",
                "duration_sec": 20,
                "assets": [{"tier": "D", "provider": "pixabay_video"}],
            },
        ],
    }

    result = longform_media_gate(board)

    assert result["passed"] is False
    assert "core scene lacks Tier A/C media" in result["reasons"][0]


def test_media_gate_passes_when_core_scenes_have_exact_or_ai_and_coverage_is_high():
    board = {
        "run_id": "longform-demo",
        "scenes": [
            {
                "n": 1,
                "role": "hook",
                "duration_sec": 20,
                "assets": [{"tier": "A", "provider": "wikimedia_image"}],
            },
            {
                "n": 2,
                "role": "evidence",
                "duration_sec": 20,
                "assets": [{"tier": "C", "provider": "veo"}],
            },
            {
                "n": 3,
                "role": "context",
                "duration_sec": 10,
                "assets": [{"tier": "B", "provider": "nasa_image"}],
            },
        ],
    }

    result = longform_media_gate(board)

    assert result["passed"] is True
    assert result["quality_runtime_ratio"] == 1.0
