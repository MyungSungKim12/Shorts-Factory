"""트렌드 리서처 에이전트 — topic.json 생성."""
import json
from datetime import datetime
from pathlib import Path

from app.console import safe_print
from app.content_format import get_content_format
from app.services.claude_client import call_agent
from app.services.web_search import search_ranking_topics


class GroundingUnavailable(RuntimeError):
    """A grounded-only research request could not obtain a verified topic."""

    def __init__(self, message: str, *, daily_quota: bool) -> None:
        super().__init__(message)
        self.daily_quota = daily_quota


def _is_daily_quota_error(error: Exception) -> bool:
    message = str(error).lower().replace(" ", "")
    return any(marker in message for marker in (
        "daily", "perday", "quotaexceeded", "일일", "할당초과",
    ))

# 회차별 스토리 방향. 무료 스톡 확보성보다 클릭 욕구와 반전을 먼저 평가한다.
SLOT_CATEGORIES = {
    1: {
        "name": "극한 생존/위험한 동물",
        "desc": "포식, 독, 기생, 극한 환경 생존처럼 본능적으로 위험과 호기심을 느끼는 이야기.",
        "examples": "몸이 잘려도 살아남는 원리, 포식자를 역으로 이용하는 생존법, 인간에게 치명적인 작은 동물",
        "visual_fallback": "wild animal survival",
    },
    2: {
        "name": "금지된 장소/거대 구조",
        "desc": "사람이 접근하기 어렵거나 상식 밖 규모와 목적을 가진 장소·구조의 이야기.",
        "examples": "지도에서 사라진 시설, 버려진 거대 구조물, 불가능해 보이는 고대 공법",
        "visual_fallback": "massive abandoned structure",
    },
    3: {
        "name": "역사적 반전/재난/치명적 실수",
        "desc": "한 번의 판단이 거대한 결과를 만든 사건, 재난, 생존과 역사적 반전 이야기.",
        "examples": "사소한 실수로 무너진 작전, 모두가 틀렸던 발견, 살아남을 수 없던 곳의 생존 기록",
        "visual_fallback": "historic disaster ruins",
    },
    4: {
        "name": "미스터리/기이한 기록",
        "desc": "처음 들으면 거짓말 같지만 검증 가능한 이상 현상, 수수께끼와 기이한 기록 이야기.",
        "examples": "흔적 없이 사라진 장소, 설명 뒤에도 더 이상해지는 현상, 현실에 남은 불가능한 기록",
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
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return [r[0] for r in rows]


def run_researcher(
    data_dir: Path,
    run_id: str = None,
    recent_topics: list = None,
    content_format: str | None = None,
    work_root: str = "work",
    use_cache: bool = True,
    verification_policy: str = "normal",
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
    if verification_policy not in {"normal", "grounded_only"}:
        raise ValueError("verification_policy must be 'normal' or 'grounded_only'")

    selected = get_content_format(content_format)
    if recent_topics is None:
        recent_topics = _load_recent_topics(data_dir)

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d")
    work_dir = data_dir / work_root / run_id
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
        safe_print(f"  · 회차 {slot} 카테고리: {category['name']}")

    # 사실 검증 규칙(AGENTS.md): 검증 방식과 근거를 항상 기록한다.
    #   1) 그라운딩 검색 성공 → 검증 + 캐시에 저장 (grounded_search)
    #   2) 그라운딩 실패 → 검증 캐시에서 재사용 (verified_cache)
    #   3) 둘 다 없으면 불변 기록·수치 소재만 보수적으로 생성 (model_memory)
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
        if use_cache:
            save_verified(data_dir, cache_slot, topic_dict)
            safe_print(f"  ✓ 검색 그라운딩으로 검증 (캐시 {cache_size(data_dir, cache_slot)}건)")
        else:
            safe_print("  ✓ 검색 그라운딩으로 검증 (샘플 모드: 캐시 미사용)")
    except Exception as e:
        if verification_policy == "grounded_only":
            raise GroundingUnavailable(
                f"grounded research unavailable: {e}",
                daily_quota=_is_daily_quota_error(e),
            ) from e
        safe_print(f"  ℹ️ 그라운딩 검증 실패({str(e)[:60]}) — 검증 캐시에서 소재 찾기")
        cache_slot = 0 if selected == "story" else slot
        cached = pick_cached(data_dir, cache_slot, recent_topics) if use_cache else None
        if cached:
            topic_dict = validate_topic(cached, selected)
            safe_print(f"  ✓ 검증 캐시 재사용: {topic_dict.get('topic', '')}")
        else:
            # 캐시도 비었으면 보수 모드(model_memory) — 규칙상 '불변 기록·수치' 소재만 허용.
            # 프롬프트가 최신 변동 소재를 배제하도록 강제한다.
            safe_print("  ℹ️ 캐시 비어있음 — 보수 모드(불변 기록만, model_memory)로 진행")
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
    verification += (
        " Include visual_identity in the JSON: exact_queries must start with "
        "`exact:` and use the verified story subject; safe_fallbacks must stay "
        "within that same real-world subject family."
    )
    recent = context.get("recent_topics") or []
    category = context.get("category") or {}
    category_block = (
        f"- 이번 회차 방향: {category.get('name')}\n"
        f"- 방향 설명: {category.get('desc')}\n"
        f"- 좋은 출발점: {category.get('examples')}"
        if category else
        "- 이번 회차 방향: 위험, 반전, 거대한 규모 중 하나가 분명한 이야기"
    )
    return f"""당신은 실재 장소·자연현상·역사 구조물·동물 생존 원리를 조사하는 한국어 Shorts 리서처다.

[목표]
- 사람들이 제목만 보고도 "왜? 어떻게?"라고 묻게 되는 소재 1개를 고른다.
- 최종 JSON을 쓰기 전에 서로 다른 후보를 반드시 8개 만든 뒤 내부에서 비교한다.
- 후보 평가 과정은 출력하지 말고 최고점 소재 하나만 JSON으로 출력한다.
- Pexels/Pixabay 무료 스톡에서 실제 대상과 주변 환경을 여러 장면으로 찾을 수 있어야 한다.
- 무료 영상 확보성은 필수 조건이지만 재미와 반전보다 먼저 소재를 결정하지 않는다.
- 최근 사용 소재와 중복하지 않는다: {recent if recent else '없음'}

[이번 회차]
{category_block}

[재미 점수 — 후보마다 각 0~5점, 총 30점]
1. 첫 3초 호기심: 설명을 듣기 전에도 결말이 궁금한가?
2. 상식 반전: 대부분의 예상과 실제 답이 다른가?
3. 위험·규모·충격: 생존, 죽음, 거대한 크기, 치명적 실수 중 하나가 있는가?
4. 남성 시청자 관심: 위험, 기술, 구조, 전쟁, 재난, 미스터리 본능을 자극하는가?
5. 무료 영상 확보: 실제 대상 또는 같은 사건군의 화면을 여러 장면 구할 수 있는가?
6. 차별성: 최근 소재와 다르고 너무 흔하게 소비된 설명이 아닌가?
- 총점 24점 이상을 목표로 한다. 모든 후보가 24점 미만이어도 추가 호출하거나 회차를 중단하지 말고, 그중 최고점 후보를 반드시 선택한다.
- 최종 JSON의 interest_score에는 최고 후보의 실제 합계를 기록한다.
- selection_reason에는 클릭을 부르는 반전·위험·규모를 한 문장으로 적는다.

[바로 탈락]
- 단순히 색이 다른 이유, 이름의 유래, 평범한 지형 형성 과정만 설명하는 소재
- 제목을 읽은 순간 답이 예상되는 교과서형 질문
- "신비롭다", "놀랍다" 같은 형용사 외에 구체적인 위험·반전·규모가 없는 소재
- 무료 스톡이 많다는 이유만으로 고른 평범한 자연 풍경이나 귀여운 동물 소개
- 이미 너무 유명해 결말까지 알려진 상투적인 미스터리

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
  "interest_score": 26,
  "selection_reason": "죽을 수 있는 극한 환경에서 상식과 반대되는 생존 원리가 드러난다",
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
  "visual_identity": {{
    "exact_queries": ["exact:desert lake", "exact:desert lake aerial"],
    "safe_fallbacks": ["desert lake aerial", "cracked desert shore"],
    "required_exact": true
  }},
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
