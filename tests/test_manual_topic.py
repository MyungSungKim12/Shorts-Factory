"""사용자 입력 소재 해석과 시각자료 사전검사 테스트."""
from __future__ import annotations

import json

import pytest

from app.services import manual_topic, media_library
from app.services.manual_topic import (
    ManualTopicInput,
    assess_visual_feasibility,
    build_requested_topic_prompt,
    check_requested_topic,
)
from app.services.media_library import MediaCandidate
from app.services.slot_reservations import events_after


def _story_topic(*, category: str = "science_mystery") -> dict:
    return {
        "format": "story",
        "topic": "1977년 단 한 번 관측된 와우 신호의 정체",
        "category": category,
        "hook_angle": "72초 동안 포착된 강한 신호는 다시 나타나지 않았다",
        "target_keyword": "Wow signal",
        "core_question": "와우 신호는 어디에서 왔는가",
        "interest_score": 27,
        "selection_reason": "한 번뿐인 우주 관측 기록의 출처가 아직 확정되지 않았다",
        "facts": [
            {
                "claim": "오하이오 주립대 전파망원경이 신호를 기록했다",
                "value": "1977년 8월 15일 약 72초 동안 관측됐다",
                "source": "Ohio State University",
                "source_url": "https://osu.edu/wow-signal",
            },
            {
                "claim": "신호는 수소선 주파수 부근에서 기록됐다",
                "value": "천문학적 전파 관측 후보로 분석됐다",
                "source": "SETI Institute",
                "source_url": "https://www.seti.org/wow-signal",
            }
        ],
        "visual_plan": [
            {
                "beat": "hook",
                "keywords": ["Wow signal printout", "Big Ear radio telescope"],
            }
        ],
        "visual_identity": {
            "exact_queries": ["exact:Wow signal", "exact:Big Ear radio telescope"],
            "safe_fallbacks": ["radio telescope night", "radio signal chart"],
            "required_exact": True,
        },
        "verification_method": "grounded_search",
        "verified_at": "2026-08-10T01:23:45+00:00",
    }


def _agent_returning(payload: dict, captured: dict | None = None):
    def fake_agent(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return json.dumps(payload, ensure_ascii=False)

    return fake_agent


def _reservable_visual() -> dict:
    return {
        "level": "high",
        "reservable": True,
        "candidate_count": 1,
        "exact_wikimedia_count": 1,
        "related_stock_count": 0,
        "reusable_ai_count": 0,
        "exact_wikimedia_available": True,
        "related_stock_available": False,
        "reusable_ai_available": False,
        "new_ai_allowed": False,
    }


def test_single_ambiguous_word_requires_two_to_three_user_choices(tmp_path):
    response = {
        "needs_clarification": True,
        "interpretations": ["조선 제6대 왕 단종", "단종된 제품 이야기"],
    }

    result = check_requested_topic(
        tmp_path,
        "20260810-1",
        ManualTopicInput(topic_input="단종"),
        call_agent_fn=_agent_returning(response),
    )

    assert result == {
        "status": "needs_input",
        "interpretations": ["조선 제6대 왕 단종", "단종된 제품 이야기"],
    }
    event = events_after(tmp_path, "20260810-1", 0)[-1]
    assert event["metadata"] == {"interpretation_count": 2}


def test_ambiguous_response_never_exposes_more_than_three_choices(tmp_path):
    response = {
        "needs_clarification": True,
        "interpretations": ["첫째", "둘째", "셋째", "넷째"],
    }

    result = check_requested_topic(
        tmp_path,
        "20260810-1",
        ManualTopicInput(topic_input="모호한 말"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["interpretations"] == ["첫째", "둘째", "셋째"]


def test_off_channel_topic_is_reservable_with_warning_when_other_checks_pass(
    tmp_path, monkeypatch
):
    captured = {}
    response = {
        "needs_clarification": False,
        "channel_fit": False,
        "safety": {"allowed": True, "reason": "일반 역사·기술 설명"},
        "topic": _story_topic(category="history_mystery"),
    }
    monkeypatch.setattr(
        manual_topic, "assess_visual_feasibility", lambda _: _reservable_visual()
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-2",
        ManualTopicInput(topic_input="단종된 게임기"),
        call_agent_fn=_agent_returning(response, captured),
    )

    assert result["status"] == "reservable"
    assert result["channel_fit"] is False
    assert result["channel_warning"] is True
    assert result["verification_method"] == "grounded_search"
    assert result["topic_payload"]["verification_method"] == "grounded_search"
    assert captured["grounded"] is True
    assert "use_search" not in captured


def test_visual_insufficiency_prevents_reservation(tmp_path, monkeypatch):
    response = {
        "needs_clarification": False,
        "channel_fit": True,
        "safety": {"allowed": True, "reason": "일반 과학 설명"},
        "topic": _story_topic(),
    }
    monkeypatch.setattr(
        manual_topic,
        "assess_visual_feasibility",
        lambda _: {
            **_reservable_visual(),
            "level": "insufficient",
            "reservable": False,
            "candidate_count": 0,
            "exact_wikimedia_count": 0,
            "exact_wikimedia_available": False,
        },
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-3",
        ManualTopicInput(topic_input="와우 신호"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["status"] == "needs_input"
    assert result["reason"] == "visual_insufficient"


def test_requested_topic_prompt_includes_constraints_references_and_recent_topics():
    request = ManualTopicInput(
        topic_input="와우 신호",
        emphasis="관측 당시 기록",
        include_text="공식 기관 출처",
        exclude_text="외계인이라고 단정",
        reference_urls=("https://www.seti.org/wow-signal",),
    )

    prompt = build_requested_topic_prompt(request, ["최근 우주 전파 신호 소재"])

    assert "와우 신호" in prompt
    assert "관측 당시 기록" in prompt
    assert "공식 기관 출처" in prompt
    assert "외계인이라고 단정" in prompt
    assert "https://www.seti.org/wow-signal" in prompt
    assert "최근 우주 전파 신호 소재" in prompt
    assert "2~3개" in prompt
    assert "채널 방향 밖" in prompt
    assert "거절하지" in prompt
    assert "최소 2개의 서로 다른" in prompt
    assert "grounded_search" in prompt
    assert "verification_method" in prompt
    assert '"safety"' in prompt
    assert '"allowed"' in prompt


def test_true_economy_topic_keeps_its_category_and_is_reservable_with_warning(
    tmp_path, monkeypatch
):
    topic = _story_topic(category="economy")
    response = {
        "needs_clarification": False,
        "channel_fit": False,
        "safety": {"allowed": True, "reason": "합법적인 경제사 설명"},
        "topic": topic,
    }
    monkeypatch.setattr(
        manual_topic, "assess_visual_feasibility", lambda _: _reservable_visual()
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-2",
        ManualTopicInput(topic_input="단종된 게임기의 중고 가격 경제"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["status"] == "reservable"
    assert result["channel_warning"] is True
    assert result["topic_payload"]["category"] == "economy"


@pytest.mark.parametrize(
    ("safety", "reason"),
    [
        ({"allowed": False, "reason": "불법 행위를 구체적으로 조장함"}, "safety_rejected"),
        (None, "safety_invalid"),
    ],
)
def test_unsafe_or_missing_safety_is_non_reservable_before_visual_search(
    tmp_path, monkeypatch, safety, reason
):
    response = {
        "needs_clarification": False,
        "channel_fit": True,
        "topic": _story_topic(),
    }
    if safety is not None:
        response["safety"] = safety
    monkeypatch.setattr(
        manual_topic,
        "assess_visual_feasibility",
        lambda _: pytest.fail("unsafe topic reached visual preflight"),
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-3",
        ManualTopicInput(topic_input="위험한 요청"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["status"] == "failed"
    assert result["reservable"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (lambda topic: topic.pop("verification_method"), "verification_method"),
        (lambda topic: topic.update(verification_method="model_memory"), "verification_method"),
        (lambda topic: topic.update(verified_at="검색 완료 시각"), "verified_at"),
        (lambda topic: topic.update(facts=topic["facts"][:1]), "distinct_sources"),
    ],
)
def test_invalid_grounding_evidence_is_failed_not_fabricated(
    tmp_path, monkeypatch, mutate, expected_reason
):
    topic = _story_topic()
    mutate(topic)
    response = {
        "needs_clarification": False,
        "channel_fit": True,
        "safety": {"allowed": True, "reason": "일반 과학 설명"},
        "topic": topic,
    }
    monkeypatch.setattr(
        manual_topic,
        "assess_visual_feasibility",
        lambda _: pytest.fail("invalid grounding reached visual preflight"),
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-4",
        ManualTopicInput(topic_input="와우 신호"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["status"] == "failed"
    assert result["reservable"] is False
    assert result["reason"] == "grounding_invalid"
    assert result["grounding_error"] == expected_reason


def test_grounded_timestamp_is_preserved_instead_of_replaced(tmp_path, monkeypatch):
    response = {
        "needs_clarification": False,
        "channel_fit": True,
        "safety": {"allowed": True, "reason": "일반 과학 설명"},
        "topic": _story_topic(),
    }
    monkeypatch.setattr(
        manual_topic, "assess_visual_feasibility", lambda _: _reservable_visual()
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-1",
        ManualTopicInput(topic_input="와우 신호"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["topic_payload"]["verified_at"] == "2026-08-10T01:23:45+00:00"


def test_reusable_ai_search_checks_all_exact_subjects_and_deduplicates(
    tmp_path, monkeypatch
):
    first = type("Asset", (), {"asset_id": "asset-first"})()
    later = type("Asset", (), {"asset_id": "asset-later"})()

    class FakeLibrary:
        def __init__(self, data_dir):
            assert data_dir == tmp_path

        def find_reusable_asset(self, subject):
            return {
                "Wow signal": None,
                "Big Ear radio telescope": later,
                "Ohio radio observatory": later,
            }.get(subject, first)

    topic = _story_topic()
    topic["visual_identity"]["exact_queries"] = [
        "exact:Wow signal",
        "exact:Big Ear radio telescope",
        "exact:Ohio radio observatory",
    ]
    topic["_data_dir"] = str(tmp_path)
    monkeypatch.setattr(manual_topic, "AiOpeningLibrary", FakeLibrary)

    assets = manual_topic.find_reusable_ai_opening(topic)

    assert [asset.asset_id for asset in assets] == ["asset-later"]


def _candidate(provider: str, media_id: str, keyword: str) -> MediaCandidate:
    return MediaCandidate(
        provider=provider,
        media_id=media_id,
        source_url=f"https://example.com/{media_id}",
        download_url=f"https://cdn.example.com/{media_id}",
        width=1080,
        height=1920,
        media_type="image" if provider == "wikimedia_image" else "video",
        keyword=keyword,
        description=media_id,
    )


def test_visual_candidate_search_is_metadata_only_and_uses_configured_stock(
    monkeypatch,
):
    monkeypatch.setenv("PEXELS_API_KEY", "configured")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(
        manual_topic,
        "_wikimedia_image_candidates",
        lambda query: [_candidate("wikimedia_image", query, query)],
    )
    monkeypatch.setattr(
        manual_topic,
        "_pexels_video_candidates",
        lambda query: [_candidate("pexels_video", query, query)],
    )
    monkeypatch.setattr(
        manual_topic,
        "_pexels_photo_candidates",
        lambda query: [],
    )
    monkeypatch.setattr(
        manual_topic,
        "_pixabay_video_candidates",
        lambda query: pytest.fail("unconfigured Pixabay provider was queried"),
    )
    monkeypatch.setattr(
        media_library,
        "_download_candidate",
        lambda *args, **kwargs: pytest.fail("preflight downloaded media"),
    )

    result = manual_topic.search_visual_candidates(_story_topic())

    assert len(result["exact_wikimedia"]) == 2
    assert len(result["related_stock"]) == 2


@pytest.mark.parametrize(
    ("visual_candidates", "reusable", "new_ai", "expected_level"),
    [
        ({"exact_wikimedia": [object()], "related_stock": []}, False, False, "high"),
        ({"exact_wikimedia": [], "related_stock": [object()]}, False, False, "medium"),
        ({"exact_wikimedia": [], "related_stock": []}, True, False, "medium"),
        ({"exact_wikimedia": [], "related_stock": []}, False, True, "medium"),
    ],
)
def test_any_relevant_visual_path_keeps_topic_reservable(
    monkeypatch, visual_candidates, reusable, new_ai, expected_level
):
    monkeypatch.setattr(
        manual_topic, "search_visual_candidates", lambda _: visual_candidates
    )
    monkeypatch.setattr(
        manual_topic, "find_reusable_ai_opening", lambda _: object() if reusable else None
    )
    monkeypatch.setattr(
        manual_topic, "new_ai_opening_permitted", lambda _: new_ai
    )

    result = assess_visual_feasibility(_story_topic())

    assert result["level"] == expected_level
    assert result["reservable"] is True


def test_visual_preflight_is_insufficient_only_when_every_relevant_path_is_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(manual_topic, "search_visual_candidates", lambda _: [])
    monkeypatch.setattr(manual_topic, "find_reusable_ai_opening", lambda _: None)
    monkeypatch.setattr(manual_topic, "new_ai_opening_permitted", lambda _: False)

    result = assess_visual_feasibility(_story_topic())

    assert result == {
        "level": "insufficient",
        "reservable": False,
        "candidate_count": 0,
        "exact_wikimedia_count": 0,
        "related_stock_count": 0,
        "reusable_ai_count": 0,
        "exact_wikimedia_available": False,
        "related_stock_available": False,
        "reusable_ai_available": False,
        "new_ai_allowed": False,
    }


@pytest.mark.parametrize("channel_fit", [None, "true", "false", 1, 0])
def test_channel_fit_warns_unless_provider_returns_literal_true(
    tmp_path, monkeypatch, channel_fit
):
    response = {
        "needs_clarification": False,
        "safety": {"allowed": True, "reason": "general science"},
        "topic": _story_topic(),
    }
    if channel_fit is not None:
        response["channel_fit"] = channel_fit
    monkeypatch.setattr(
        manual_topic, "assess_visual_feasibility", lambda _: _reservable_visual()
    )

    result = check_requested_topic(
        tmp_path,
        "20260810-1",
        ManualTopicInput(topic_input="literal boolean only"),
        call_agent_fn=_agent_returning(response),
    )

    assert result["channel_fit"] is False
    assert result["channel_warning"] is True
