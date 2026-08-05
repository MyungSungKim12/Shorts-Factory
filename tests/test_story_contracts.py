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
    roles = [
        "hook", "context", "problem", "mechanism", "mechanism",
        "mechanism", "payoff", "payoff", "close",
    ]
    scenes = [{
        "n": n,
        "role": roles[n - 1],
        "narration": "검증된 기록이 중요한 단서를 보여줍니다.",
        "visuals": ["desert lake aerial", "desert water closeup"],
        "duration_sec": 8,
        "emphasis": ["호수"],
    } for n in range(1, 10)]
    data = {
        "format": "story",
        "title": "사막의 호수는 왜 마르지 않을까",
        "description": "검증된 장소 이야기",
        "tags": ["사막", "호수"],
        "hook": "비가 없는데 호수가 마르지 않습니다.",
        "scenes": scenes,
        "cta": "",
        "total_duration_sec": 72,
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
    assert validate_script(story_script())["total_duration_sec"] == 72


def test_story_contract_accepts_scene_narration_up_to_80_characters():
    data = story_script()
    data["scenes"][0]["narration"] = "가" * 39 + "," + "나" * 39 + "."

    assert validate_script(data, "story")["scenes"][0]["narration"].endswith(".")


def test_story_contract_rejects_scene_without_terminal_punctuation():
    data = story_script()
    data["scenes"][0]["narration"] = "문맥이 끝났지만 종결 부호가 없는 문장입니다"

    with pytest.raises(ValueError, match="종결 문장부호"):
        validate_script(data, "story")


def test_story_contract_accepts_one_complete_clause_within_scene_limit():
    data = story_script()
    data["scenes"][0]["narration"] = "가" * 46 + "."

    assert validate_script(data, "story")["scenes"][0]["narration"].endswith(".")


def test_story_contract_accepts_concise_body():
    data = story_script()
    for scene in data["scenes"]:
        scene["narration"] = "가" * 20 + "," + "나" * 20 + "."

    assert validate_script(data, "story")["format"] == "story"


def test_story_contract_rejects_body_over_400_characters():
    data = story_script()
    for scene in data["scenes"]:
        scene["narration"] = "가" * 38 + "," + "나" * 37 + "."

    with pytest.raises(ValueError, match="400자 상한"):
        validate_script(data, "story")


@pytest.mark.parametrize(
    "title",
    [
        "100자 이하 제목: 지하 수정 동굴의 비밀",
        "제목: 지하 수정 동굴의 비밀",
        "글자 수 50자 이내 지하 수정 동굴의 비밀",
    ],
)
def test_story_title_rejects_prompt_instruction_leak(title):
    with pytest.raises(ValueError, match="제목 지시문"):
        validate_script(story_script(title=title), "story")


@pytest.mark.parametrize(
    "category",
    ["place_nature", "science_mystery", "hidden_world", "history_mystery"],
)
def test_story_contract_accepts_mystery_channel_categories(category):
    assert validate_topic(story_topic(category=category))["category"] == category


def test_story_contract_rejects_removed_animal_category():
    with pytest.raises(ValueError):
        validate_topic(story_topic(category="animal_survival"))


def test_story_topic_derives_a_visual_identity_for_legacy_cached_documents():
    topic = validate_topic(story_topic())

    assert topic["visual_identity"]["exact_queries"]
    assert topic["visual_identity"]["safe_fallbacks"]


def test_story_contract_accepts_body_duration_reserved_for_cta():
    data = story_script()
    durations = [8, 8, 8, 8, 8, 8, 8, 8, 8]
    for scene, duration in zip(data["scenes"], durations):
        scene["duration_sec"] = duration
    data["total_duration_sec"] = 72

    assert validate_script(data)["total_duration_sec"] == 72


def test_story_contract_accepts_shorter_body_reserved_for_spoken_intro():
    data = story_script()
    durations = [8, 8, 8, 8, 8, 8, 8, 8, 8]
    for scene, duration in zip(data["scenes"], durations):
        scene["duration_sec"] = duration
    data["total_duration_sec"] = 72

    assert validate_script(data)["total_duration_sec"] == 72


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
