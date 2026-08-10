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
