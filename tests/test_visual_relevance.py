"""Verified-subject visual anchors for story media selection."""

import pytest

from app.models import validate_topic
from app.services.visual_relevance import ensure_visual_identity, story_scene_queries


def _story_topic():
    return {
        "format": "story",
        "topic": "Why a desert lake does not dry up",
        "category": "place_nature",
        "hook_angle": "A lake survives where rain almost never falls",
        "target_keyword": "desert lake",
        "core_question": "Where does the lake's water come from?",
        "facts": [{
            "claim": "Groundwater supply",
            "value": "Groundwater supplies the lake.",
            "source": "Public geology agency",
            "source_url": "https://example.com/geology",
        }],
        "visual_plan": [
            {"beat": "hook", "keywords": ["desert lake aerial", "dry lake shore"]},
            {"beat": "mechanism", "keywords": ["desert groundwater spring", "lake water closeup"]},
        ],
        "verification_method": "grounded_search",
        "verified_at": "2026-07-20T12:00:00+09:00",
    }


def _script():
    return {
        "scenes": [
            {"n": 1, "role": "hook", "visuals": ["desert lake aerial", "dry lake shore"]},
            {"n": 2, "role": "context", "visuals": ["desert water closeup", "dry lake shore"]},
            {"n": 3, "role": "close", "visuals": ["desert lake sunset", "desert lake aerial"]},
        ]
    }


def test_missing_visual_identity_is_derived_from_verified_topic():
    topic = validate_topic(_story_topic(), "story")

    identity = topic["visual_identity"]

    assert identity["exact_queries"][0].startswith("exact:")
    assert identity["safe_fallbacks"]
    assert identity["required_exact"] is True


def test_blank_exact_query_derives_the_verified_target_anchor():
    topic = _story_topic()
    topic["visual_identity"] = {
        "exact_queries": [""],
        "safe_fallbacks": ["desert lake aerial"],
    }

    identity = ensure_visual_identity(topic)["visual_identity"]

    assert identity["exact_queries"][0] == "exact: desert lake"


def test_story_contract_rejects_blank_visual_identity_queries():
    topic = _story_topic()
    topic["visual_identity"] = {
        "exact_queries": [""],
        "safe_fallbacks": ["desert lake aerial"],
    }

    with pytest.raises(ValueError):
        validate_topic(topic, "story")


def test_hook_and_close_queries_keep_subject_anchor():
    script = _script()

    queries = story_scene_queries(script, _story_topic())

    assert queries[1][0].startswith("exact:")
    assert queries[len(script["scenes"])][0].startswith("exact:")


def test_middle_queries_keep_scene_visuals_before_safe_fallbacks():
    topic = validate_topic(_story_topic(), "story")

    queries = story_scene_queries(_script(), topic)

    assert queries[2][:2] == ["desert water closeup", "dry lake shore"]
    assert set(queries[2][2:]) <= set(topic["visual_identity"]["safe_fallbacks"])
