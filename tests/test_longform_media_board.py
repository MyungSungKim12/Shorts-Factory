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
    scenes = []
    roles = [
        "hook", "context", "evidence", "mechanism", "evidence",
        "counterpoint", "mechanism", "payoff", "evidence", "context",
        "mechanism", "counterpoint", "evidence", "payoff", "mechanism",
        "context", "evidence", "counterpoint", "payoff", "close",
    ]
    for index, role in enumerate(roles, start=1):
        asset = (
            {"tier": "C", "provider": "veo", "media_type": "video"}
            if role in {"hook", "evidence", "mechanism", "payoff"}
            else {"tier": "B", "provider": "pexels_video", "media_type": "video"}
        )
        scenes.append(
            {
                "n": index,
                "role": role,
                "duration_sec": 18,
                "assets": [asset],
            }
        )
    board = {
        "run_id": "longform-demo",
        "scenes": scenes,
    }

    result = longform_media_gate(board)

    assert result["passed"] is True
    assert result["quality_runtime_ratio"] == 1.0
    assert result["video_scene_count"] == 20


def test_media_gate_rejects_longform_with_too_few_video_scenes():
    scenes = []
    roles = [
        "hook", "context", "evidence", "mechanism", "evidence",
        "counterpoint", "mechanism", "payoff", "evidence", "context",
        "mechanism", "counterpoint", "evidence", "payoff", "mechanism",
        "context", "evidence", "counterpoint", "payoff", "close",
    ]
    for index, role in enumerate(roles, start=1):
        media_type = "video" if index <= 14 else "image"
        provider = "pexels_video" if media_type == "video" else "wikimedia_image"
        tier = "C" if role in {"hook", "evidence", "mechanism", "payoff"} else "B"
        scenes.append(
            {
                "n": index,
                "role": role,
                "duration_sec": 18,
                "assets": [{"tier": tier, "provider": provider, "media_type": media_type}],
            }
        )
    board = {"run_id": "longform-demo", "scenes": scenes}

    result = longform_media_gate(board)

    assert result["passed"] is False
    assert "video scenes below 15" in result["reasons"]
