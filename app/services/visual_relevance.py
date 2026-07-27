"""Derive and apply verified subject anchors for story-media searches."""
from __future__ import annotations


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    """Return non-empty queries once, preserving their supplied order."""
    result: list[str] = []
    for value in values:
        query = (value or "").strip()
        if query and query not in result:
            result.append(query)
            if limit is not None and len(result) >= limit:
                break
    return result


def _exact(query: str) -> str:
    value = (query or "").strip()
    if value.startswith("exact:"):
        value = value.removeprefix("exact:").strip()
    return f"exact: {value}" if value else ""


def _subject_anchor(identity: dict) -> str:
    for query in identity.get("exact_queries", []):
        value = (query or "").removeprefix("exact:").strip()
        if value:
            return value
    return ""


def _anchored_stock_query(query: str, anchor: str) -> str:
    value = (query or "").strip()
    if not value or value.startswith("exact:") or not anchor:
        return value
    if anchor.casefold() in value.casefold():
        return value
    return f"{anchor} {value}"


def ensure_visual_identity(topic: dict) -> dict:
    """Add deterministic anchors to a validated story topic when absent.

    The values only reuse already verified topic metadata, so this compatibility
    path cannot add a new claim or real-world subject to a cached topic.
    """
    result = dict(topic)
    supplied = result.get("visual_identity") or {}
    visual_keywords = _unique([
        keyword
        for beat in result.get("visual_plan", [])
        for keyword in beat.get("keywords", [])
    ])
    exact_queries = _unique([
        _exact(query)
        for query in supplied.get("exact_queries", [])
    ], limit=3)
    if not exact_queries:
        exact_queries = _unique([
            _exact(result.get("target_keyword", "")),
            _exact(result.get("topic", "")),
            *[_exact(keyword) for keyword in visual_keywords],
        ], limit=3)

    safe_fallbacks = _unique(supplied.get("safe_fallbacks", []), limit=5)
    if not safe_fallbacks:
        safe_fallbacks = _unique(visual_keywords, limit=5)

    result["visual_identity"] = {
        "exact_queries": exact_queries,
        "safe_fallbacks": safe_fallbacks,
        "required_exact": supplied.get("required_exact", True),
    }
    return result


def story_scene_queries(script: dict, topic: dict) -> dict[int, list[str]]:
    """Build media-search candidates without modifying ``script.json`` data."""
    identity = ensure_visual_identity(topic)["visual_identity"]
    exact_queries = identity["exact_queries"]
    anchor = _subject_anchor(identity)
    safe_fallbacks = [
        _anchored_stock_query(query, anchor)
        for query in identity["safe_fallbacks"]
    ]
    queries: dict[int, list[str]] = {}

    for scene in script.get("scenes", []):
        scene_queries = _unique([
            _anchored_stock_query(query, anchor)
            for query in scene.get("visuals", [])
        ])
        if scene.get("role") in {"hook", "close"}:
            scene_queries = _unique([
                *exact_queries,
                *scene_queries,
                *safe_fallbacks,
            ])
        else:
            scene_queries = _unique([*scene_queries, *safe_fallbacks])
        queries[int(scene["n"])] = scene_queries

    return queries
