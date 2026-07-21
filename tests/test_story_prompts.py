"""스토리 리서치·대본 프롬프트와 작가 라우팅 테스트."""
import json
import asyncio

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
    roles = ["hook", "context", "problem", "mechanism", "mechanism", "payoff", "payoff", "close"]
    return {
        "format": "story",
        "title": "사막의 호수는 왜 마르지 않을까",
        "description": "검증된 장소 이야기",
        "tags": ["사막", "호수"],
        "hook": "비가 없는데 호수가 마르지 않습니다.",
        "scenes": [{
            "n": n, "role": roles[n - 1],
            "narration": f"검증된 내용을 설명하는 {n}번째 문장입니다.",
            "visuals": ["desert lake aerial", "desert water closeup"],
            "duration_sec": 8, "emphasis": ["호수"],
        } for n in range(1, 9)],
        "cta": "이런 자연의 비밀이 더 궁금하다면, 구독과 좋아요 부탁드립니다.",
        "total_duration_sec": 64,
    }


def test_research_prompt_requires_sources_and_visual_plan():
    prompt = researcher._story_researcher_prompt({"recent_topics": []}, grounded=True)
    assert "source_url" in prompt
    assert "verification_method" in prompt
    assert "visual_plan" in prompt
    assert "실재 장소·자연현상" in prompt
    assert "최신 뉴스" in prompt


def test_writer_prompt_contains_retention_beats():
    prompt = writer._story_writer_prompt(_topic())
    assert "완성 영상 목표는 60~75초" in prompt
    assert "duration_sec 합계는 반드시 53~58초" in prompt
    assert "구독" in prompt
    assert "좋아요" in prompt
    assert "7~10개" in prompt
    assert "12~15초" in prompt
    assert "25~30초" in prompt
    assert "45~50초" in prompt
    assert '"visuals"' in prompt
    assert "인사" in prompt
    assert "exact:" in prompt
    assert "Wikimedia Commons" in prompt
    assert "close 본문에는 \"\uad6c독\"과 \"좋아요\"를 절대 넣지 마라" in prompt


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
    assert "60~75초" in captured["prompt"]
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

    def fake_researcher(data_dir, run_id, content_format=None):
        seen["researcher"] = content_format
        return {"topic": "스토리 소재", "facts": []}

    def fake_writer(data_dir, run_id, content_format=None):
        seen["writer"] = content_format
        return {"title": "스토리 영상 제목", "scenes": [], "total_duration_sec": 64}

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
