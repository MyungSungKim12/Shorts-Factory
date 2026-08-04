"""스토리 리서치·대본 프롬프트와 작가 라우팅 테스트."""
import json
import asyncio
import sqlite3
from datetime import datetime

from app.agents import orchestrator, researcher, writer


def _topic():
    return {
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
        "visual_plan": [{"beat": "hook", "keywords": ["desert lake aerial", "dry lake shore"]}],
        "verification_method": "grounded_search",
        "verified_at": "2026-07-20T12:00:00+09:00",
    }


def _script():
    roles = [
        "hook", "context", "problem", "mechanism", "mechanism",
        "mechanism", "payoff", "payoff", "close",
    ]
    return {
        "format": "story",
        "title": "사막의 호수는 왜 마르지 않을까",
        "description": "검증된 장소 이야기",
        "tags": ["사막", "호수"],
        "hook": "비가 없는데 호수가 마르지 않습니다.",
        "scenes": [{
            "n": n, "role": roles[n - 1],
            "narration": "검증된 기록은 중요한 단서를 분명하게 보여줍니다, 이 수치가 뜻하는 범위와 아직 남은 의문을 차례대로 설명합니다.",
            "visuals": ["desert lake aerial", "desert water closeup"],
            "duration_sec": 8, "emphasis": ["호수"],
        } for n in range(1, 10)],
        "cta": "이런 자연의 비밀이 더 궁금하다면, 구독과 좋아요 부탁드립니다.",
        "total_duration_sec": 72,
    }


def test_research_prompt_requires_sources_and_visual_plan():
    prompt = researcher._story_researcher_prompt({"recent_topics": []}, grounded=True)
    assert "source_url" in prompt
    assert "verification_method" in prompt
    assert "visual_plan" in prompt
    assert "visual_identity" in prompt
    assert "검증 가능한 자연·과학" in prompt
    assert "최신 뉴스" in prompt
    assert "이상한 지구기록" in prompt
    assert "동물 중심 소재는 선택하지 마라" in prompt
    assert "science_mystery" in prompt
    assert "hidden_world" in prompt
    assert "history_mystery" in prompt


def test_recent_topics_include_both_uploaded_title_and_original_topic(tmp_path):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute(
        "CREATE TABLE videos (video_id TEXT, date TEXT, title TEXT, topic TEXT, status TEXT)"
    )
    db.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?)",
        (
            "video-1",
            datetime.now().strftime("%Y%m%d") + "-1",
            "빛 없는 심해의 비밀",
            "빛도 산소도 없는 심해 4,000m 생명체의 비밀",
            "uploaded",
        ),
    )
    db.commit()
    db.close()

    recent = researcher._load_recent_topics(tmp_path)

    assert recent == [
        "빛 없는 심해의 비밀",
        "빛도 산소도 없는 심해 4,000m 생명체의 비밀",
    ]


def test_research_prompt_requires_selected_domain_and_semantic_deduplication():
    prompt = researcher._story_researcher_prompt(
        {
            "recent_topics": ["기존 우주 신호 소재"],
            "focus_domain": {
                "name": "대기·기상",
                "desc": "극한 기상과 설명하기 어려운 대기 관측",
                "examples": "상층 번개, 원통 구름",
            },
        },
        grounded=True,
    )

    assert "대기·기상" in prompt
    assert "상층 번개" in prompt
    assert "핵심 대상·사건·관측값" in prompt


def test_writer_prompt_contains_retention_beats():
    prompt = writer._story_writer_prompt(_topic())
    assert "완성 영상 목표는 65~80초" in prompt
    assert "560~680자" in prompt
    assert "80자 이하" in prompt
    assert "duration_sec 합계는 반드시 72~84초" in prompt
    assert "구독" in prompt
    assert "좋아요" in prompt
    assert "9~10개" in prompt
    assert "문장부호 없이 여러 절을 이어 쓰지" in prompt
    assert "종결 문장부호" in prompt
    assert "12~15초" in prompt
    assert "25~30초" in prompt
    assert "45~50초" in prompt
    assert "60~70초" in prompt
    assert '"visuals"' in prompt
    assert "인사" in prompt
    assert "exact:" in prompt
    assert "visual_identity" in prompt
    assert "Wikimedia Commons" in prompt
    assert "NARRATIVE_PATTERN:" in prompt
    assert "CHANNEL_EDITORIAL_VIEW" in prompt
    assert "SUBJECT_ANCHORED_VISUALS" in prompt
    assert "close 본문에는 \"\uad6c독\"과 \"좋아요\"를 절대 넣지 마라" in prompt
    assert '"title": "100자 이하 제목"' not in prompt
    assert '"title": "10분만 머물러도 위험한 지하 수정 동굴의 비밀"' in prompt


def test_writer_routes_story_format_and_saves_validated_json(tmp_path, monkeypatch):
    run_id = "sample-story"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "topic.json").write_text(json.dumps(_topic(), ensure_ascii=False), encoding="utf-8")
    captured = {}

    def fake_call_agent(**kwargs):
        captured.update(kwargs)
        return json.dumps(_script(), ensure_ascii=False)

    monkeypatch.setattr(writer, "call_agent", fake_call_agent)
    result = writer.run_writer(tmp_path, run_id, content_format="story")

    assert result["format"] == "story"
    assert "65~80초" in captured["prompt"]
    assert json.loads((work_dir / "script.json").read_text(encoding="utf-8"))["format"] == "story"


def test_writer_regenerates_once_when_model_returns_incomplete_json(tmp_path, monkeypatch):
    run_id = "retry-incomplete-story"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "topic.json").write_text(
        json.dumps(_topic(), ensure_ascii=False), encoding="utf-8"
    )
    prompts = []

    def fake_call_agent(**kwargs):
        prompts.append(kwargs["prompt"])
        if len(prompts) == 1:
            return '{"format":"story","title":"truncated'
        return json.dumps(_script(), ensure_ascii=False)

    monkeypatch.setattr(writer, "call_agent", fake_call_agent)

    result = writer.run_writer(tmp_path, run_id, content_format="story")

    assert result["format"] == "story"
    assert len(prompts) == 2
    assert "RETRY_JSON_ONLY" in prompts[1]
    assert "560~680자" in prompts[1]
    assert "더 짧고" not in prompts[1]


def test_writer_uses_verified_template_after_two_invalid_responses(tmp_path, monkeypatch):
    run_id = "verified-template-story"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    topic = _topic()
    (work_dir / "topic.json").write_text(
        json.dumps(topic, ensure_ascii=False), encoding="utf-8"
    )
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return '{"format":"story","title":"truncated'

    monkeypatch.setattr(writer, "call_agent", fake_call_agent)

    result = writer.run_writer(tmp_path, run_id, content_format="story")

    assert len(calls) == 2
    assert result["writer_mode"] == "verified_template"
    assert len(result["scenes"]) == 9
    assert 72 <= result["total_duration_sec"] <= 84
    narration = " ".join(scene["narration"] for scene in result["scenes"])
    assert 560 <= sum(len(scene["narration"]) for scene in result["scenes"]) <= 680
    assert all(scene["narration"].endswith((".", "?", "!")) for scene in result["scenes"])
    assert topic["facts"][0]["claim"] in narration
    assert topic["facts"][0]["value"] in narration
    allowed_visuals = {
        keyword
        for item in topic["visual_plan"]
        for keyword in item["keywords"]
    }
    assert {
        visual for scene in result["scenes"] for visual in scene["visuals"]
    } <= allowed_visuals


def test_verified_template_stays_in_contract_with_long_verified_facts():
    topic = _topic()
    topic.update({
        "topic": "핀란드 지하 500미터, 십만 년 후까지 안전해야 할 인류의 마지막 유산",
        "hook_angle": "지금 만든 경고가 십만 년 뒤 사람에게도 같은 뜻으로 전달될 수 있을까요",
        "core_question": "인류는 어떻게 핵폐기물을 십만 년 동안 지하에서 안전하게 격리할 수 있을까요",
    })
    topic["facts"] = [
        {
            "claim": f"검증된 장기 보관 시설의 {index}번째 안전 설계 기록은 여러 방벽을 함께 사용한다고 설명한다",
            "value": "공개 자료에는 금속 용기와 점토층과 안정적인 암반이 서로 다른 단계에서 물질의 이동을 막는다고 기록되어 있다",
            "source": "공공 안전기관",
            "source_url": f"https://example.com/safety/{index}",
        }
        for index in range(1, 7)
    ]

    script = writer.build_verified_story_script(topic)
    lengths = [len(scene["narration"]) for scene in script["scenes"]]

    assert max(lengths) <= 75
    assert 560 <= sum(lengths) <= 680
    assert writer.validate_script(script, "story")["format"] == "story"


def test_ranking_writer_still_uses_existing_prompt(tmp_path, monkeypatch):
    run_id = "ranking"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    topic = {
        "topic": "세계에서 높은 산 TOP 3", "ranking_size": 3,
        "items": [{"rank": r, "name": f"산{r}", "fact": f"{r}미터", "source": "기관"} for r in (1, 2, 3)],
        "verification_method": "grounded_search",
    }
    (work_dir / "topic.json").write_text(json.dumps(topic, ensure_ascii=False), encoding="utf-8")
    captured = {}

    def fake_call_agent(**kwargs):
        captured.update(kwargs)
        return json.dumps({
            "title": "세계에서 높은 산 TOP 3",
            "scenes": [
                {"n": 1, "rank": 3, "narration": "3위 설명", "duration_sec": 3},
                {"n": 2, "rank": 2, "narration": "2위 설명", "duration_sec": 3},
                {"n": 3, "rank": 1, "narration": "1위 설명", "duration_sec": 3},
            ],
            "total_duration_sec": 9,
        }, ensure_ascii=False)

    monkeypatch.setattr(writer, "call_agent", fake_call_agent)
    writer.run_writer(tmp_path, run_id, content_format="ranking")
    assert "랭킹 숏츠 전문" in captured["prompt"]


def test_orchestrator_passes_selected_format_to_researcher_and_writer(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("CONTENT_FORMAT", "story")
    run_id = f"{datetime.now().strftime('%Y%m%d')}-1"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "prepared.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scheduled_at": "2026-07-21T11:00:00+09:00",
                "quality_gate": {"passed": True},
            }
        ),
        encoding="utf-8",
    )

    def fake_researcher(data_dir, run_id, content_format=None):
        seen["researcher"] = content_format
        return {"topic": "스토리 소재", "facts": []}

    def fake_writer(data_dir, run_id, content_format=None):
        seen["writer"] = content_format
        return {
            "title": "스토리 영상 제목", "scenes": [], "total_duration_sec": 64,
            "writer_mode": "verified_template",
        }

    async def fake_producer(*args, **kwargs):
        seen["producer"] = kwargs.get("content_format")
        return {"output_file": str(tmp_path / "output.mp4"), "actual_duration": 64}

    monkeypatch.setattr(orchestrator, "run_researcher", fake_researcher)
    monkeypatch.setattr(orchestrator, "run_writer", fake_writer)
    monkeypatch.setattr(orchestrator, "run_producer", fake_producer)
    monkeypatch.setattr(orchestrator, "run_uploader", lambda *args: {"status": "skipped", "reason": "test"})

    result = asyncio.run(orchestrator.run_pipeline(tmp_path, "ffmpeg", slot=1))
    assert seen == {"researcher": "story", "writer": "story", "producer": "story"}
    assert result["content_format"] == "story"
    assert result["prepared"]["quality_gate"]["passed"] is True
    assert result["stages"]["writer"]["writer_mode"] == "verified_template"


def test_orchestrator_ignores_unpassed_prepared_marker(tmp_path):
    run_id = f"{datetime.now().strftime('%Y%m%d')}-1"
    work_dir = tmp_path / "work" / run_id
    work_dir.mkdir(parents=True)
    (work_dir / "prepared.json").write_text(
        json.dumps({"run_id": run_id, "quality_gate": {"passed": False}}),
        encoding="utf-8",
    )

    assert orchestrator._load_prepared_marker(work_dir, run_id) is None


def test_orchestrator_without_prepared_marker_uses_just_in_time_generation(
    tmp_path, monkeypatch
):
    calls = []

    def fake_researcher(*args, **kwargs):
        calls.append("researcher")
        return {"topic": "즉시 생성 소재", "items": []}

    def fake_writer(*args, **kwargs):
        calls.append("writer")
        return {"title": "즉시 생성 대본", "scenes": [], "total_duration_sec": 0}

    async def fake_producer(*args, **kwargs):
        calls.append("producer")
        return {"output_file": str(tmp_path / "output.mp4")}

    monkeypatch.setattr(orchestrator, "run_researcher", fake_researcher)
    monkeypatch.setattr(orchestrator, "run_writer", fake_writer)
    monkeypatch.setattr(orchestrator, "run_producer", fake_producer)
    monkeypatch.setattr(
        orchestrator,
        "run_uploader",
        lambda *args: {"status": "skipped", "reason": "test"},
    )

    result = asyncio.run(orchestrator.run_pipeline(tmp_path, "ffmpeg", slot=1))

    assert calls == ["researcher", "writer", "producer"]
    assert "prepared" not in result


def test_sample_researcher_skips_sqlite_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        researcher,
        "call_agent",
        lambda **kwargs: json.dumps(_topic(), ensure_ascii=False),
    )
    result = researcher.run_researcher(
        tmp_path,
        "isolated",
        recent_topics=[],
        content_format="story",
        work_root="samples",
        use_cache=False,
    )
    assert result["verification_method"] == "grounded_search"
    assert (tmp_path / "samples" / "isolated" / "topic.json").exists()
    assert not (tmp_path / "videos.sqlite").exists()
