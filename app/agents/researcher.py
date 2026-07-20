"""트렌드 리서처 에이전트 — topic.json 생성."""
import json
from datetime import datetime
from pathlib import Path

from app.content_format import get_content_format
from app.services.claude_client import call_agent
from app.services.web_search import search_ranking_topics

# 회차별 고정 카테고리 — 매일 4개 영상이 서로 다른 결로 나오고, 카테고리별 성과 비교(A/B)도 됨.
# desc는 "무료 스톡(Pexels) 영상이 존재하는 대상"으로 유도하는 게 핵심.
SLOT_CATEGORIES = {
    1: {
        "name": "동물/펫",
        "desc": "강아지·고양이·아기동물·희귀동물·귀여운 동물의 순위. "
                "전 연령이 좋아하고 공유가 잘 되는 대중적 소재.",
        "examples": "가장 비싼 반려견 품종, 가장 큰 고양이 품종, 가장 오래 사는 동물, "
                    "가장 빠른 동물, 가장 귀여운 아기동물",
        "visual_fallback": "cute animal",   # 검색 실패 시 안전 대체 영상어
    },
    2: {
        "name": "여행/명소",
        "desc": "가고 싶은 도시·해변·야경·랜드마크·이색 명소의 순위. "
                "20~30대 버킷리스트 소구, 스톡 영상 풍부.",
        "examples": "죽기 전 꼭 가봐야 할 여행지, 세계에서 가장 아름다운 해변, "
                    "야경이 예쁜 도시, 이색적인 호텔",
        "visual_fallback": "travel landscape",
    },
    3: {
        "name": "역사",
        "desc": "고대 문명·유적·왕조·역사적 사건·발명의 순위. "
                "스토리성이 강해 체류시간 유리. 시각은 유적·유물·자연 등 일반 스톡으로 표현.",
        "examples": "가장 오래된 문명, 역사상 가장 거대했던 제국, 세계 7대 불가사의, "
                    "가장 오래된 건축물",
        "visual_fallback": "ancient ruins",
    },
    4: {
        "name": "미스터리",
        "desc": "미해결 사건·불가사의·기이한 현상·수수께끼의 순위. "
                "궁금증 유발이 커 끝까지 보게 함. 분위기 있는 일반 스톡(안개·심해·우주 등)으로 표현.",
        "examples": "아직도 못 푼 세계의 미스터리, 사라진 문명, 설명 불가능한 자연현상, "
                    "미스터리한 심해 생물",
        "visual_fallback": "dark foggy atmosphere",
    },
}


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


def run_researcher(
    data_dir: Path,
    run_id: str = None,
    recent_topics: list = None,
    content_format: str | None = None,
) -> dict:
    """
    랭킹 소재를 발굴하고 순위 데이터를 수집한다.

    Args:
        data_dir: 데이터 저장 경로
        run_id: 작업 단위 식별자 (예: "20260713-2", None이면 오늘 날짜)
        recent_topics: 최근 사용 소재 목록 (None이면 업로드 DB에서 자동 조회)

    Returns:
        topic.json 스키마 dict
    """
    selected = get_content_format(content_format)
    if recent_topics is None:
        recent_topics = _load_recent_topics(data_dir)

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d")
    work_dir = data_dir / "work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # 회차(run_id 끝의 -N)로 카테고리 결정
    slot = None
    if "-" in run_id:
        try:
            slot = int(run_id.rsplit("-", 1)[1])
        except ValueError:
            slot = None
    category = SLOT_CATEGORIES.get(slot)

    # 프롬프트에 전달할 컨텍스트
    context = {
        "ranking_size": 5,
        "recent_topics": recent_topics,
        "category": category,
    }
    if category:
        print(f"  · 회차 {slot} 카테고리: {category['name']}")

    # 사실 검증 규칙(CLAUDE.md): 검증된 소재만 업로드. 검증 경로는 2가지뿐:
    #   1) 그라운딩 검색 성공 → 검증 + 캐시에 저장 (grounded_search)
    #   2) 그라운딩 실패 → 검증 캐시에서 재사용 (verified_cache)
    #   둘 다 안 되면 회차 중단 (model_memory 업로드 금지)
    from app.models import validate_topic
    from app.services.fact_cache import save_verified, pick_cached, cache_size
    from app.services.json_extract import extract_json

    topic_dict = None
    try:
        topic = call_agent(
            prompt=(
                _story_researcher_prompt(context, grounded=True)
                if selected == "story" else _researcher_prompt(context, grounded=True)
            ),
            agent_name="trend-researcher",
            grounded=True,
        )
        raw_topic = extract_json(topic)
        raw_topic["verification_method"] = "grounded_search"
        raw_topic["verified_at"] = datetime.now().isoformat()
        topic_dict = validate_topic(raw_topic, selected)
        cache_slot = 0 if selected == "story" else slot
        save_verified(data_dir, cache_slot, topic_dict)
        print(f"  ✓ 검색 그라운딩으로 검증 (캐시 {cache_size(data_dir, cache_slot)}건)")
    except Exception as e:
        print(f"  ℹ️ 그라운딩 검증 실패({str(e)[:60]}) — 검증 캐시에서 소재 찾기")
        cache_slot = 0 if selected == "story" else slot
        cached = pick_cached(data_dir, cache_slot, recent_topics)
        if cached:
            topic_dict = validate_topic(cached, selected)
            print(f"  ✓ 검증 캐시 재사용: {topic_dict.get('topic', '')}")
        else:
            # 캐시도 비었으면 보수 모드(model_memory) — 규칙상 '불변 기록·수치' 소재만 허용.
            # 프롬프트가 최신 변동 소재를 배제하도록 강제한다.
            print("  ℹ️ 캐시 비어있음 — 보수 모드(불변 기록만, model_memory)로 진행")
            topic = call_agent(
                prompt=(
                    _story_researcher_prompt(context, grounded=False)
                    if selected == "story" else _researcher_prompt(context, grounded=False)
                ),
                agent_name="trend-researcher",
                grounded=False,
            )
            raw_topic = extract_json(topic)
            raw_topic["verification_method"] = "model_memory"
            raw_topic["verified_at"] = datetime.now().isoformat()
            topic_dict = validate_topic(raw_topic, selected)

    # 업로드 가능 검증 방식인지 최종 확인 (방어)
    from app.models import UPLOADABLE_VERIFICATION
    if topic_dict.get("verification_method") not in UPLOADABLE_VERIFICATION:
        raise RuntimeError(f"허용되지 않은 검증 방식({topic_dict.get('verification_method')})")

    # topic.json 저장 (검증 통과분만 저장됨)
    topic_file = work_dir / "topic.json"
    topic_file.write_text(json.dumps(topic_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return topic_dict


def _story_researcher_prompt(context: dict, grounded: bool = True) -> str:
    """무료 스톡으로 표현 가능한 단일 소재를 사실 검증하는 스토리 프롬프트."""
    verification = (
        "검색 결과를 근거로 최소 2개의 공공기관·대학·박물관·학술기관 출처를 교차 확인하라."
        if grounded else
        "시간이 지나도 바뀌지 않는 불변 사실만 사용하고, 확실한 공식 출처 URL을 아는 소재만 선택하라."
    )
    recent = context.get("recent_topics") or []
    return f"""당신은 실재 장소·자연현상·역사 구조물·동물 생존 원리를 조사하는 한국어 Shorts 리서처다.

[목표]
- 하나의 강한 질문으로 60~75초 설명이 가능한 소재 1개를 고른다.
- Pexels/Pixabay 무료 스톡에서 실제 대상과 주변 환경을 여러 장면으로 찾을 수 있어야 한다.
- 우선 비율은 실재 장소·자연현상 70%, 역사 구조물 20%, 동물 생존 10%다.
- 최근 사용 소재와 중복하지 않는다: {recent if recent else '없음'}

[금지]
- 최신 뉴스, 실시간 순위, 연예인, 기업 실적, 스포츠 결과처럼 변하는 소재
- 영화·방송·CCTV처럼 저작권 영상이 필요한 소재
- 검색으로 확인하지 못한 수치나 인과관계 추측

[사실 검증]
{verification}
- 각 facts 항목에 claim, value, 실제 기관명 source, 직접 확인 가능한 source_url을 기록한다.
- 검색을 사용했으면 verification_method는 grounded_search다.
- visual_plan에는 스토리 비트별 구체적인 영어 검색어를 2~3개 쓴다.

[JSON만 출력]
{{
  "format": "story",
  "topic": "사막 한가운데 호수가 마르지 않는 이유",
  "category": "place_nature",
  "hook_angle": "비가 거의 오지 않는데 호수는 남아 있다",
  "target_keyword": "desert lake",
  "core_question": "물은 어디에서 공급되는가",
  "facts": [
    {{
      "claim": "검증된 주장",
      "value": "검증된 설명 또는 수치",
      "source": "공공기관 또는 학술기관명",
      "source_url": "https://기관의-직접-출처"
    }}
  ],
  "visual_plan": [
    {{"beat": "hook", "keywords": ["desert lake aerial", "cracked desert shore"]}}
  ],
  "verification_method": "grounded_search",
  "verified_at": "검색 완료 시각"
}}

category는 place_nature, history_structure, animal_survival 중 하나만 사용하라.
"""


def _researcher_prompt(context: dict, grounded: bool = True) -> str:
    """리서처 프롬프트 — grounded=False면 검색 없이 확실한 불변 기록만 쓰는 보수 모드."""
    if grounded:
        step3 = f"3. 최고점 소재 1개를 골라 **구글 검색으로 순위 데이터를 확인**하고 TOP {context['ranking_size']}를 완성하라."
        fact_rules = """[사실 검증 규칙 — 가장 중요]
- 각 항목의 fact(수치)와 순위는 반드시 검색 결과에 근거하라. 기억이 아니라 검색이 기준이다.
- source에는 검색으로 확인한 실제 출처(매체/기관명), source_url에는 그 출처의 실제 URL을 적어라.
- 검색으로 확인하지 못한 항목은 목록에 넣지 말라 — 항목을 채우려고 추측하는 것은 금지.
- fact에는 반드시 구체적 수치를 넣어라 (스코빌 지수, 미터, km/h, 판매량 등).
- 모든 항목의 name/fact/source/source_url을 실제 내용으로 채워라. "..."나 빈 값은 절대 금지."""
    else:
        step3 = f"3. 최고점 소재 1개를 골라, 당신이 확실히 아는 데이터로만 TOP {context['ranking_size']}를 완성하라."
        fact_rules = """[사실성 규칙 — 가장 중요 (검색 불가 모드)]
- 확실히 아는 사실만 사용하라. 순위나 수치가 조금이라도 불확실한 소재는 후보에서 제외하라.
- 시간이 지나도 변하지 않는 기록/수치 기반 소재만 허용 (예: 산 높이, 바다 깊이, 스코빌 지수).
  최신 순위 변동이 있는 소재(구독자 수, 매출 순위 등)는 금지.
- fact에는 반드시 구체적 수치를 넣어라.
- 모든 항목의 name/fact/source를 실제 내용으로 채워라. "..."나 빈 값은 절대 금지."""

    category = context.get("category")
    if category:
        cat_block = (
            f"- 카테고리: **{category['name']}** — {category['desc']}\n"
            f"  반드시 이 카테고리 안에서 소재를 골라라. 예시: {category['examples']}"
        )
        cat_step1 = f"1. **{category['name']}** 카테고리 안에서 랭킹 소재 후보를 4개 이상 떠올려라."
    else:
        cat_block = "- 포맷: 주제 무관 랭킹"
        cat_step1 = ("1. 랭킹 소재 후보를 4개 이상 떠올려라.\n"
                     "   (예: 세계에서 가장 매운 고추, 가장 깊은 바다, 가장 빠른 동물 등)")

    return f"""당신은 랭킹 콘텐츠 소재 발굴 전문가다. 순위를 매길 수 있고, 1위가 궁금해지는 소재만 고른다.

[채널 정보]
- 포맷: TOP {context['ranking_size']} 랭킹 숏츠 (한국어)
{cat_block}
- 최근 14일 사용 소재(중복 금지): {context['recent_topics'] if context['recent_topics'] else '없음'}

[작업]
{cat_step1}
2. 각 후보를 점수화하라:
   - 1위 의외성(0-5): 사람들이 예상한 1위와 실제 1위가 다른가?
   - 대중성(0-5): 사전지식 없이 이해 가능한가?
   - 영상 확보성(0-5): 항목들이 음식/동물/자연/건축/탈것처럼 무료 스톡 영상이 존재하는 대상인가?
     (특정 인물/게임/브랜드 제품은 스톡이 없어 감점)
{step3}

{fact_rules}

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
    {{"rank": 5, "name": "하바네로", "fact": "스코빌 지수 최대 35만", "source": "위키백과", "source_url": "https://ko.wikipedia.org/wiki/하바네로", "visual_keyword": "habanero pepper orange"}},
    {{"rank": 4, "name": "고스트 페퍼", "fact": "스코빌 지수 약 100만, 2007년 기네스 기록", "source": "기네스 세계기록", "source_url": "https://www.guinnessworldrecords.com/", "visual_keyword": "ghost pepper red"}}
  ],
  "evidence": ["스코빌 지수라는 객관적 측정 기준 존재", "1위가 대중 예상과 다름"],
  "verification_note": "스코빌 지수는 공인된 측정값으로 신뢰도 높음"
}}
"""
