"""롱폼 장면별 미디어 적합도와 품질 게이트."""
from __future__ import annotations

from collections.abc import Iterable


CORE_SCENE_ROLES = frozenset({"hook", "evidence", "mechanism", "payoff"})
QUALITY_TIERS = frozenset({"A", "B", "C"})
MIN_VIDEO_SCENES = 15


def media_tier_for_source(source: dict) -> str:
    """Return A/B/C/D tier for a source-like metadata dictionary.

    A: exact real media, B: strong contextual media, C: reference-based AI,
    D: generic/weak stock or unknown media.
    """
    if not isinstance(source, dict):
        return "D"

    explicit = str(source.get("tier") or "").strip().upper()
    if explicit in {"A", "B", "C", "D"}:
        return explicit

    provider = str(source.get("provider") or "").strip().lower()
    if source.get("asset_id") and (
        source.get("source_reference")
        or source.get("reference_source_url")
        or source.get("source_url")
    ):
        return "C"

    if provider in {"wikimedia_image", "nasa_image"} and source.get("exact_match"):
        return "A"

    if provider in {
        "wikimedia_image",
        "nasa_image",
        "pexels_video",
        "pexels_image",
        "pixabay_video",
        "pixabay_image",
    }:
        return "B" if source.get("strong_match") else "D"

    return "D"


def _runtime_for_scene(scene: dict, assets: Iterable[dict]) -> float:
    scene_duration = float(scene.get("duration_sec") or 0)
    asset_durations = [
        float(asset.get("duration_sec") or 0)
        for asset in assets
        if isinstance(asset, dict) and float(asset.get("duration_sec") or 0) > 0
    ]
    return max(scene_duration, max(asset_durations, default=0.0))


def scene_media_quality(scene: dict, assets: list[dict]) -> dict:
    """Summarize the visual strength of one longform scene."""
    tiers = [media_tier_for_source(asset) for asset in assets]
    rank = {"A": 4, "C": 3, "B": 2, "D": 1}
    best_tier = max(tiers, key=lambda tier: rank[tier]) if tiers else "D"
    role = str(scene.get("role") or "").strip().lower()
    selected = assets[0] if assets else {}
    selected_media_type = str(selected.get("media_type") or "").strip().lower()
    selected_provider = str(selected.get("provider") or "").strip().lower()
    selected_is_video = selected_media_type == "video" or selected_provider in {
        "pexels_video",
        "pixabay_video",
        "veo",
        "vertex_veo",
        "veo-3.1-fast-generate-001",
    }
    return {
        "scene": int(scene.get("n") or 0),
        "role": role,
        "runtime_sec": round(_runtime_for_scene(scene, assets), 2),
        "best_tier": best_tier,
        "tiers": tiers,
        "selected_media_type": selected_media_type,
        "selected_is_video": selected_is_video,
        "core_scene": role in CORE_SCENE_ROLES,
        "core_has_exact_or_ai": any(tier in {"A", "C"} for tier in tiers),
        "quality_media": any(tier in QUALITY_TIERS for tier in tiers),
    }


def longform_media_gate(media_board: dict) -> dict:
    """Validate whether a longform media board is strong enough to render."""
    scenes = media_board.get("scenes") if isinstance(media_board, dict) else []
    if not isinstance(scenes, list) or not scenes:
        return {
            "passed": False,
            "reasons": ["media board has no scenes"],
            "quality_runtime_ratio": 0.0,
        }

    reasons: list[str] = []
    total_runtime = 0.0
    quality_runtime = 0.0
    video_scene_count = 0
    scene_results = []
    for scene in scenes:
        assets = scene.get("assets") if isinstance(scene, dict) else []
        if not isinstance(assets, list):
            assets = []
        quality = scene_media_quality(scene, assets)
        scene_results.append(quality)
        total_runtime += quality["runtime_sec"]
        if quality["selected_is_video"]:
            video_scene_count += 1
        if quality["quality_media"]:
            quality_runtime += quality["runtime_sec"]
        if quality["core_scene"] and not quality["core_has_exact_or_ai"]:
            reasons.append(
                f"core scene lacks Tier A/C media: scene {quality['scene']}"
            )

    first_scene = scene_results[0]
    if not first_scene["core_has_exact_or_ai"]:
        reasons.append("first scene lacks exact or reference-based media")

    ratio = round(quality_runtime / total_runtime, 3) if total_runtime else 0.0
    if ratio < 0.6:
        reasons.append(f"quality media runtime below 60%: {ratio:.1%}")
    if len(scene_results) >= 20 and video_scene_count < MIN_VIDEO_SCENES:
        reasons.append(f"video scenes below {MIN_VIDEO_SCENES}")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "quality_runtime_ratio": ratio,
        "video_scene_count": video_scene_count,
        "min_video_scenes": MIN_VIDEO_SCENES,
        "scenes": scene_results,
    }
