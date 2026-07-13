"""트렌드 리서처 에이전트 — topic.json 생성."""
import json
from datetime import datetime
from pathlib import Path

from app.services.claude_client import call_agent
from app.services.web_search import search_ranking_topics


def _load_recent_topics(data_dir: Path, days: int = 14) -> list:
    """최근 업로드된 영상 제목을 DB에서 조회 (소재 중복 방지용)."""
    import sqlite3
    from datetime import timedelta

    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    db = sqlite3.connect(db_file)
    try:
        rows = db.execute(
            "SELECT title FROM videos WHERE date >= ? ORDER BY date DESC", (cutoff,)
        ).fetchall()
    finally:
        db.close()
    return [r[0] for r in rows]


def run_researcher(data_dir: Path, run_id: str = None, recent_topics: list = None) -> dict:
    """
    랭킹 소재를 발굴하고 순위 데이터를 수집한다.

    Args:
        data_dir: 데이터 저장 경로
        run_id: 작업 단위 식별자 (예: "20260713-2", None이면 오늘 날짜)
        recent_topics: 최근 사용 소재 목록 (None이면 업로드 DB에서 자동 조회)

    Returns:
        topic.json 스키마 dict
    """
    if recent_topics is None:
        recent_topics = _load_recent_topics(data_dir)

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d")
    work_dir = data_dir / "work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # 프롬프트에 전달할 컨텍스트
    context = {
        "ranking_size": 5,
        "recent_topics": recent_topics,
    }

    # Gemini + Google 검색 그라운딩 — 실제 검색 결과에 근거한 순위 데이터 수집
    topic = call_agent(
        prompt=_researcher_prompt(context),
        agent_name="trend-researcher",
        grounded=True,
    )

    # JSON 파싱 (그라운딩 모드는 JSON 강제가 안 되므로 블록 추출 폴백 필수)
    try:
        topic_dict = json.loads(topic)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{.*\}', topic, re.DOTALL)
        if match:
            topic_dict = json.loads(match.group())
        else:
            raise ValueError(f"리서처 응답을 JSON으로 파싱할 수 없음:\n{topic}")

    # 검증 게이트 — 순위 완결성/자리표시자/출처 누락 검사. 실패 시 파이프라인 중단.
    from app.models import validate_topic
    topic_dict = validate_topic(topic_dict)

    # topic.json 저장 (검증 통과분만 저장됨)
    topic_file = work_dir / "topic.json"
    topic_file.write_text(json.dumps(topic_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return topic_dict


def _researcher_prompt(context: dict) -> str:
    """리서처 에이전트의 고효율 프롬프트."""
    return f"""당신은 랭킹 콘텐츠 소재 발굴 전문가다. 순위를 매길 수 있고, 1위가 궁금해지는 소재만 고른다.

[채널 정보]
- 포맷: TOP {context['ranking_size']} 랭킹 숏츠 (한국어, 주제 무관)
- 최근 14일 사용 소재(중복 금지): {context['recent_topics'] if context['recent_topics'] else '없음'}

[작업]
1. 랭킹 소재 후보를 4개 이상 떠올려라.
   (예: 세계에서 가장 매운 고추 — 스코빌 지수, 가장 깊은 바다, 가장 빠른 동물 등
   객관적 수치·기록이 존재하는 분야)
2. 각 후보를 점수화하라:
   - 1위 의외성(0-5): 사람들이 예상한 1위와 실제 1위가 다른가?
   - 대중성(0-5): 사전지식 없이 이해 가능한가?
   - 영상 확보성(0-5): 항목들이 음식/동물/자연/건축/탈것처럼 무료 스톡 영상이 존재하는 대상인가?
     (특정 인물/게임/브랜드 제품은 스톡이 없어 감점)
3. 최고점 소재 1개를 골라 **구글 검색으로 순위 데이터를 확인**하고 TOP {context['ranking_size']}를 완성하라.

[사실 검증 규칙 — 가장 중요]
- 각 항목의 fact(수치)와 순위는 반드시 검색 결과에 근거하라. 기억이 아니라 검색이 기준이다.
- source에는 검색으로 확인한 실제 출처(매체/기관명)를 적어라.
- 검색으로 확인하지 못한 항목은 목록에 넣지 말라 — 항목을 채우려고 추측하는 것은 금지.
- fact에는 반드시 구체적 수치를 넣어라 (스코빌 지수, 미터, km/h, 판매량 등).
- 모든 항목의 name/fact/source를 실제 내용으로 채워라. "..."나 빈 값은 절대 금지.

[제약]
- 정답이 없는 주관 순위 금지 (예: 가장 예쁜 연예인)
- visual_keyword는 무료 스톡 영상 검색용 영어 단어 2~3개 (예: "spicy pepper red")
- topic, hook_angle, name, fact는 모두 한국어로 작성

[출력 — 아래 JSON 스키마로만, ranking_size개 항목 전부 채워서]
{{
  "topic": "세계에서 가장 매운 고추 TOP 5",
  "ranking_size": {context['ranking_size']},
  "hook_angle": "1위는 청양고추의 400배",
  "target_keyword": "매운 고추 순위",
  "items": [
    {{"rank": 5, "name": "하바네로", "fact": "스코빌 지수 최대 35만", "source": "스코빌 지수 공식 측정", "visual_keyword": "habanero pepper orange"}},
    {{"rank": 4, "name": "고스트 페퍼", "fact": "스코빌 지수 약 100만, 2007년 기네스 기록", "source": "기네스 세계기록", "visual_keyword": "ghost pepper red"}}
  ],
  "evidence": ["스코빌 지수라는 객관적 측정 기준 존재", "1위가 대중 예상과 다름"],
  "verification_note": "스코빌 지수는 공인된 측정값으로 신뢰도 높음"
}}
"""
