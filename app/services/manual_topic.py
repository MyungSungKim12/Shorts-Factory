"""Grounded interpretation and metadata-only visual preflight for manual topics."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.agents.researcher import _load_recent_topics, requested_topic_contract_prompt
from app.models import validate_manual_story_topic
from app.services.ai_opening_library import AiOpeningLibrary
from app.services.claude_client import call_agent
from app.services.credit_guard import paid_features_enabled
from app.services.json_extract import extract_json
from app.services.media_library import (
    _pexels_photo_candidates,
    _pexels_video_candidates,
    _pixabay_video_candidates,
    _wikimedia_image_candidates,
    exact_candidate_matches,
    stock_candidate_matches,
)
from app.services.slot_reservations import append_slot_event


@dataclass(frozen=True)
class ManualTopicInput:
    topic_input: str
    emphasis: str = ""
    include_text: str = ""
    exclude_text: str = ""
    reference_urls: tuple[str, ...] = ()


def _display(value: str) -> str:
    text = str(value or "").strip()
    return text if text else "없음"


def build_requested_topic_prompt(
    request: ManualTopicInput, recent_topics: list[str]
) -> str:
    """Build a grounded prompt without changing automatic topic selection."""
    references = "\n".join(f"- {url}" for url in request.reference_urls) or "없음"
    recent = "\n".join(f"- {topic}" for topic in recent_topics) or "없음"
    return f"""당신은 '이상한 지구기록' 채널의 사용자 요청 소재 리서처다.
사용자의 표현을 먼저 해석하고, 뜻이 분명할 때만 검색 그라운딩으로 사실을 검증한다.

[사용자 입력]
- 소재: {_display(request.topic_input)}
- 강조점: {_display(request.emphasis)}
- 반드시 포함: {_display(request.include_text)}
- 제외: {_display(request.exclude_text)}
- 참고 URL:
{references}

[최근 14일 중복 제외]
{recent}
- 제목이 달라도 핵심 대상·사건·관측값이 같으면 선택하지 않는다.

[판정 규칙]
- 한 단어처럼 뜻이 모호하면 조사 결과를 확정하지 말고 가능한 구체적 해석 2~3개를 제시한다.
- 의미가 명확하면 검색 결과를 근거로 최소 2개의 서로 다른 공공기관·대학·박물관·학술기관 출처를 교차 확인한다.
- 검색으로 확인하지 못한 수치나 인과관계는 쓰지 않는다.
- 참고 URL은 단서일 뿐이며 그 내용도 검색 결과와 출처로 확인한다.
- 채널 방향 밖 소재는 channel_fit=false로 표시하되 그 이유만으로 거절하지 않는다.
- 저작권 영상에 의존하지 않고 실제 대상의 Wikimedia 호환 검색어와 관련 무료 스톡 검색어를 만든다.

{requested_topic_contract_prompt()}

설명이나 마크다운 없이 JSON 하나만 출력하라."""


def _unique_candidates(candidates: list) -> list:
    seen: set[str] = set()
    result = []
    for candidate in candidates:
        key = getattr(candidate, "unique_id", repr(candidate))
        if key not in seen:
            result.append(candidate)
            seen.add(key)
    return result


def search_visual_candidates(topic: dict) -> dict[str, list]:
    """Search provider metadata only; never fetch a candidate's media bytes."""
    identity = topic.get("visual_identity") or {}
    exact_candidates = []
    for exact_query in identity.get("exact_queries") or []:
        query = str(exact_query or "").removeprefix("exact:").strip()
        if not query:
            continue
        exact_candidates.extend(
            candidate
            for candidate in _wikimedia_image_candidates(query)
            if exact_candidate_matches(exact_query, candidate)
        )

    stock_collectors = []
    if os.getenv("PEXELS_API_KEY", "").strip():
        stock_collectors.extend((_pexels_video_candidates, _pexels_photo_candidates))
    if os.getenv("PIXABAY_API_KEY", "").strip():
        stock_collectors.append(_pixabay_video_candidates)
    stock_candidates = []
    for query in identity.get("safe_fallbacks") or []:
        clean_query = str(query or "").strip()
        if not clean_query:
            continue
        for collector in stock_collectors:
            stock_candidates.extend(
                candidate
                for candidate in collector(clean_query)
                if stock_candidate_matches(clean_query, candidate)
            )
    return {
        "exact_wikimedia": _unique_candidates(exact_candidates),
        "related_stock": _unique_candidates(stock_candidates),
    }


def _preflight_data_dir(topic: dict) -> Path:
    value = topic.get("_data_dir") or os.getenv("DATA_DIR", "./data")
    return Path(value)


def _subject_keys(topic: dict) -> list[str]:
    identity = topic.get("visual_identity") or {}
    subjects = []
    seen = set()
    for query in identity.get("exact_queries") or []:
        subject = str(query or "").removeprefix("exact:").strip()
        key = subject.casefold()
        if subject and key not in seen:
            subjects.append(subject)
            seen.add(key)
    if not subjects:
        subject = str(topic.get("target_keyword") or topic.get("topic") or "").strip()
        if subject:
            subjects.append(subject)
    return subjects


def find_reusable_ai_opening(topic: dict):
    """Return deduplicated reusable assets found across every exact subject."""
    subjects = _subject_keys(topic)
    if not subjects:
        return []
    library = AiOpeningLibrary(_preflight_data_dir(topic))
    result = []
    seen = set()
    for subject in subjects:
        asset = library.find_reusable_asset(subject)
        asset_id = str(getattr(asset, "asset_id", "") or "")
        if asset is not None and asset_id and asset_id not in seen:
            result.append(asset)
            seen.add(asset_id)
    return result


def new_ai_opening_permitted(topic: dict) -> bool:
    """Report whether current credit mode permits one later AI generation."""
    explicit = topic.get("ai_opening_allowed")
    if isinstance(explicit, bool):
        return explicit
    return paid_features_enabled(_preflight_data_dir(topic))


def assess_visual_feasibility(topic: dict) -> dict:
    """Return counts and booleans without downloading or generating final media."""
    candidates = search_visual_candidates(topic)
    if isinstance(candidates, dict):
        exact = list(candidates.get("exact_wikimedia") or [])
        stock = list(candidates.get("related_stock") or [])
    else:
        exact = []
        stock = list(candidates or [])
    reusable = find_reusable_ai_opening(topic)
    new_ai = bool(new_ai_opening_permitted(topic))
    exact_count = len(exact)
    stock_count = len(stock)
    if isinstance(reusable, (list, tuple, set)):
        reusable_count = len(reusable)
    else:
        reusable_count = int(reusable is not None)
    if exact_count:
        level = "high"
    elif stock_count or reusable_count or new_ai:
        level = "medium"
    else:
        level = "insufficient"
    return {
        "level": level,
        "reservable": level != "insufficient",
        "candidate_count": exact_count + stock_count,
        "exact_wikimedia_count": exact_count,
        "related_stock_count": stock_count,
        "reusable_ai_count": reusable_count,
        "exact_wikimedia_available": exact_count > 0,
        "related_stock_available": stock_count > 0,
        "reusable_ai_available": reusable_count > 0,
        "new_ai_allowed": new_ai,
    }


def _interpretations(raw: dict) -> list[str]:
    choices = []
    for value in raw.get("interpretations") or []:
        text = str(value or "").strip()
        if text and text not in choices:
            choices.append(text)
    if not 2 <= len(choices):
        raise ValueError("ambiguous topic response requires 2-3 interpretations")
    return choices[:3]


def _source_summary(topic: dict) -> list[dict[str, str]]:
    result = []
    for fact in topic.get("facts") or []:
        source = str(fact.get("source") or "").strip()
        url = str(fact.get("source_url") or "").strip()
        item = {"source": source, "source_url": url}
        if item not in result:
            result.append(item)
    return result


def _safety_result(raw: dict) -> dict | None:
    safety = raw.get("safety")
    if not isinstance(safety, dict) or not isinstance(safety.get("allowed"), bool):
        return None
    reason = " ".join(str(safety.get("reason") or "").split())[:300]
    if not reason:
        return None
    return {"allowed": safety["allowed"], "reason": reason}


def _grounding_error(topic: object) -> str | None:
    if not isinstance(topic, dict):
        return "topic"
    if topic.get("verification_method") != "grounded_search":
        return "verification_method"
    verified_at = topic.get("verified_at")
    if not isinstance(verified_at, str):
        return "verified_at"
    try:
        timestamp = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError:
        return "verified_at"
    if timestamp.tzinfo is None:
        return "verified_at"
    facts = topic.get("facts")
    if not isinstance(facts, list) or len(facts) < 2:
        return "distinct_sources"
    distinct_sources = set()
    for fact in facts:
        if not isinstance(fact, dict):
            return "fact_source_linkage"
        if not all(str(fact.get(key) or "").strip() for key in ("claim", "value", "source")):
            return "fact_source_linkage"
        source_url = str(fact.get("source_url") or "").strip()
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "fact_source_linkage"
        distinct_sources.add(parsed.hostname.casefold().removeprefix("www."))
    if len(distinct_sources) < 2:
        return "distinct_sources"
    return None


def validate_reservable_check_result(result: object) -> dict:
    """Revalidate a persisted topic-check result before reusing its topic."""
    if not isinstance(result, dict):
        raise ValueError("saved check result must be an object")
    if result.get("status") != "reservable" or result.get("reservable") is not True:
        raise ValueError("saved check result is not reservable")
    visual = result.get("visual")
    if not isinstance(visual, dict) or visual.get("reservable") is not True:
        raise ValueError("saved visual check is not reservable")
    safety = result.get("safety")
    if not isinstance(safety, dict) or safety.get("allowed") is not True:
        raise ValueError("saved safety check is not allowed")

    topic = validate_manual_story_topic(result.get("topic_payload"))
    grounding_error = _grounding_error(topic)
    if grounding_error is not None:
        raise ValueError(f"saved grounding is invalid: {grounding_error}")
    if result.get("verification_method") != topic["verification_method"]:
        raise ValueError("saved verification metadata does not match the topic")
    normalized = result.get("normalized_topic")
    if not isinstance(normalized, str) or normalized.strip() != topic["topic"]:
        raise ValueError("saved normalized topic does not match the topic")
    if result.get("sources") != _source_summary(topic):
        raise ValueError("saved source summary does not match the topic facts")
    return {**result, "normalized_topic": normalized.strip(), "topic_payload": topic}


def _failed_result(
    data_dir: Path,
    run_id: str,
    reason: str,
    *,
    safety: dict,
    grounding_error: str | None = None,
) -> dict:
    result = {
        "status": "failed",
        "reservable": False,
        "reason": reason,
        "safety": safety,
    }
    metadata = {"status": "failed", "reason": reason}
    if grounding_error:
        result["grounding_error"] = grounding_error
        metadata["grounding_error"] = grounding_error
    append_slot_event(
        data_dir,
        run_id,
        "topic_check",
        "warning",
        "소재 안전성 또는 사실 검증 조건을 충족하지 못했습니다",
        metadata,
    )
    return result


def check_requested_topic(
    data_dir: Path,
    run_id: str,
    request: ManualTopicInput,
    *,
    call_agent_fn=call_agent,
) -> dict:
    """Interpret, ground, validate, and preflight one user-entered topic."""
    append_slot_event(
        data_dir, run_id, "topic_check", "info", "입력 소재를 해석하고 있습니다"
    )
    raw = extract_json(
        call_agent_fn(
            prompt=build_requested_topic_prompt(request, _load_recent_topics(data_dir)),
            agent_name="manual-topic-researcher",
            prefer="gemini",
            grounded=True,
        )
    )
    if raw.get("needs_clarification"):
        choices = _interpretations(raw)
        append_slot_event(
            data_dir,
            run_id,
            "topic_check",
            "info",
            "소재 의미를 선택해 주세요",
            {"interpretation_count": len(choices)},
        )
        return {"status": "needs_input", "interpretations": choices}

    safety = _safety_result(raw)
    if safety is None:
        return _failed_result(
            data_dir,
            run_id,
            "safety_invalid",
            safety={"allowed": False, "reason": "missing_or_invalid"},
        )
    if not safety["allowed"]:
        return _failed_result(
            data_dir, run_id, "safety_rejected", safety=safety
        )

    grounded_topic = raw.get("topic")
    grounding_error = _grounding_error(grounded_topic)
    if grounding_error:
        return _failed_result(
            data_dir,
            run_id,
            "grounding_invalid",
            safety=safety,
            grounding_error=grounding_error,
        )
    try:
        topic = validate_manual_story_topic(grounded_topic)
    except ValueError:
        return _failed_result(
            data_dir,
            run_id,
            "grounding_invalid",
            safety=safety,
            grounding_error="topic_contract",
        )
    visual = assess_visual_feasibility({**topic, "_data_dir": str(data_dir)})
    # Provider output is untrusted JSON.  Only the literal JSON boolean true
    # can suppress the warning; missing, numeric, and string lookalikes fail closed.
    channel_fit = raw.get("channel_fit") is True
    reservable = bool(visual.get("reservable"))
    result = {
        "status": "reservable" if reservable else "needs_input",
        "reservable": reservable,
        "normalized_topic": topic["topic"],
        "core_question": topic["core_question"],
        "channel_fit": channel_fit,
        "channel_warning": not channel_fit,
        "verification_method": topic["verification_method"],
        "safety": safety,
        "sources": _source_summary(topic),
        "visual": visual,
        "topic_payload": topic,
    }
    if not reservable:
        result["reason"] = "visual_insufficient"
    append_slot_event(
        data_dir,
        run_id,
        "topic_check",
        "warning" if not reservable or not channel_fit else "info",
        "소재 확인이 완료되었습니다" if reservable else "관련 시각자료가 부족합니다",
        {
            "status": result["status"],
            "channel_warning": result["channel_warning"],
            "visual_level": visual.get("level"),
            "visual_candidate_count": visual.get("candidate_count", 0),
            "verification_method": result["verification_method"],
        },
    )
    return result
