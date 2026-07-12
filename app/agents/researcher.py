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


def run_researcher(data_dir: Path, recent_topics: list = None) -> dict:
    """
    랭킹 소재를 발굴하고 순위 데이터를 수집한다.

    Args:
        data_dir: 데이터 저장 경로
        recent_topics: 최근 사용 소재 목록 (None이면 업로드 DB에서 자동 조회)

    Returns:
        topic.json 스키마 dict
    """
    if recent_topics is None:
        recent_topics = _load_recent_topics(data_dir)

    date_str = datetime.now().strftime("%Y%m%d")
    work_dir = data_dir / "work" / date_str
    work_dir.mkdir(parents=True, exist_ok=True)

    # 프롬프트에 전달할 컨텍스트
    context = {
        "ranking_size": 5,
        "recent_topics": recent_topics,
    }

    # Claude를 통해 리서처 에이전트 실행
    topic = call_agent(
        prompt=_researcher_prompt(context),
        agent_name="trend-researcher",
    )

    # JSON 파싱
    try:
        topic_dict = json.loads(topic)
    except json.JSONDecodeError:
        # 응답에서 JSON 블록 추출
        import re
        match = re.search(r'\{.*\}', topic, re.DOTALL)
        if match:
            topic_dict = json.loads(match.group())
        else:
            raise ValueError(f"리서처 응답을 JSON으로 파싱할 수 없음:\n{topic}")

    # topic.json 저장
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
1. 당신이 순위와 수치를 확실히 알고 있는 랭킹 소재 후보를 4개 이상 떠올려라.
   (예: 세계에서 가장 매운 고추 — 스코빌 지수, 가장 깊은 바다, 가장 빠른 동물, 가장 많이 팔린 게임 등
   객관적 수치·기록이 존재하는 분야)
2. 각 후보를 점수화하라:
   - 1위 의외성(0-5): 사람들이 예상한 1위와 실제 1위가 다른가?
   - 대중성(0-5): 사전지식 없이 이해 가능한가?
   - 사실 확신도(0-5): 순위와 수치를 당신이 확실히 알고 있는가?
3. 최고점 소재 1개를 골라 TOP {context['ranking_size']} 순위를 완성하라.

[사실성 규칙 — 가장 중요]
- 확실히 아는 사실만 사용하라. 순위나 수치가 불확실한 소재는 후보에서 제외하라.
- 최신 트렌드·시사보다 시간이 지나도 변하지 않는 기록/수치 기반 소재를 우선하라.
  (지식 기준 시점 이후 바뀌었을 수 있는 순위는 피하라 — 예: 현재 구독자 1위 유튜버)
- fact에는 반드시 구체적 수치를 넣어라 (스코빌 지수, 미터, km/h, 판매량 등).
- 모든 항목의 name/fact를 실제 내용으로 채워라. "..."나 빈 값은 절대 금지.

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
