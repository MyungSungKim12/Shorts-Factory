"""스토리형 topic/script 계약과 포맷 선택 회귀 테스트."""
import pytest

from app.content_format import get_content_format
from app.models import validate_script, validate_topic


def story_topic(**overrides):
    data = {
        "format": "story",
        "topic": "사막 한가운데 호수가 마르지 않는 이유",
        "category": "place_nature",
        "hook_angle": "비가 거의 없는데 물은 남아 있다",
        "target_keyword": "desert lake",
        "core_question": "물은 어디에서 오는가",
        "facts": [{
            "claim": "지하수 공급",
            "value": "지하 대수층에서 물이 공급된다",
            "source": "공공 지질기관",
            "source_url": "https://example.com/geology",
        }],
        "visual_plan": [{
            "beat": "hook",
            "keywords": ["desert lake aerial", "dry lake shore"],
        }],
        "verification_method": "grounded_search",
        "verified_at": "2026-07-20T12:00:00+09:00",
    }
    data.update(overrides)
    return data


def story_script(**overrides):
    roles = ["hook", "context", "problem", "mechanism", "mechanism", "payoff", "payoff", "close"]
    scenes = [{
        "n": n,
        "role": roles[n - 1],
        "narration": f"검증된 내용을 설명하는 {n}번째 문장입니다.",
        "visuals": ["desert lake aerial", "desert water closeup"],
        "duration_sec": 8,
        "emphasis": ["호수"],
    } for n in range(1, 9)]
    data = {
        "format": "story",
        "title": "사막의 호수는 왜 마르지 않을까",
        "description": "검증된 장소 이야기",
        "tags": ["사막", "호수"],
        "hook": "비가 없는데 호수가 마르지 않습니다.",
        "scenes": scenes,
        "cta": "",
        "total_duration_sec": 64,
    }
    data.update(overrides)
    return data


def test_default_format_preserves_ranking(monkeypatch):
    monkeypatch.delenv("CONTENT_FORMAT", raising=False)
    assert get_content_format() == "ranking"


def test_explicit_story_format_is_selected():
    assert get_content_format(" STORY ") == "story"


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError, match="CONTENT_FORMAT"):
        get_content_format("unknown")


def test_story_contracts_accept_complete_documents():
    assert validate_topic(story_topic())["format"] == "story"
    assert validate_script(story_script())["total_duration_sec"] == 64


def test_story_contract_accepts_body_duration_reserved_for_cta():
    data = story_script()
    durations = [8, 8, 8, 8, 8, 8, 9]
    data["scenes"] = data["scenes"][:7]
    for scene, duration in zip(data["scenes"], durations):
        scene["duration_sec"] = duration
    data["scenes"][-1]["role"] = "close"
    data["total_duration_sec"] = 57

    assert validate_script(data)["total_duration_sec"] == 57


def test_story_contract_accepts_shorter_body_reserved_for_spoken_intro():
    data = story_script()
    durations = [7, 7, 7, 8, 8, 8, 8]
    data["scenes"] = data["scenes"][:7]
    for scene, duration in zip(data["scenes"], durations):
        scene["duration_sec"] = duration
    data["scenes"][-1]["role"] = "close"
    data["total_duration_sec"] = 53

    assert validate_script(data)["total_duration_sec"] == 53


def test_story_rejects_missing_source_url():
    data = story_topic()
    data["facts"][0]["source_url"] = ""
    with pytest.raises(ValueError):
        validate_topic(data)


def test_story_rejects_wrong_duration_or_scene_count():
    data = story_script()
    data["scenes"] = data["scenes"][:6]
    with pytest.raises(ValueError):
        validate_script(data)


def test_story_rejects_nonsequential_scene_numbers():
    data = story_script()
    data["scenes"][3]["n"] = 9
    with pytest.raises(ValueError, match="씬 번호"):
        validate_script(data)


def test_story_rejects_scene_with_too_few_visual_keywords():
    data = story_script()
    data["scenes"][0]["visuals"] = ["desert lake"]
    with pytest.raises(ValueError):
        validate_script(data)


def test_story_requires_hook_first_and_close_last():
    data = story_script()
    data["scenes"][0]["role"] = "context"
    with pytest.raises(ValueError, match="hook"):
        validate_script(data)


def test_legacy_ranking_document_remains_valid():
    data = {
        "topic": "세계에서 가장 높은 산 TOP 3",
        "ranking_size": 3,
        "items": [
            {"rank": rank, "name": f"산{rank}", "fact": f"높이 {rank}미터", "source": "기관"}
            for rank in (1, 2, 3)
        ],
        "verification_method": "grounded_search",
    }
    assert validate_topic(data)["ranking_size"] == 3
