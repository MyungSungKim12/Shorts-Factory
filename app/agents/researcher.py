"""트렌드 리서처 에이전트 — topic.json 생성."""
import json
from datetime import datetime
from pathlib import Path

from app.console import safe_print
from app.content_format import get_content_format
from app.models import is_rejected_story_topic
from app.services.claude_client import call_agent
from app.services.research_feedback import build_research_feedback, topic_duplicate_reason
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

# 새 미스터리 영상의 조회·좋아요 상위 두 소재군을 하루 두 번씩 운영한다.
_SCIENCE_MYSTERY = {
    "name": "과학의 경계/미해결 관측",
    "category": "science_mystery",
    "desc": "관측과 실험 기록은 분명하지만 원인·신호·결과 해석이 아직 완전히 합의되지 않은 과학 미스터리.",
    "examples": "극한 자연 관측, 예상과 달랐던 지상 탐사 결과, 설명 후보가 여러 개인 실험 기록",
    "visual_fallback": "unexplained scientific observation",
}
_HIDDEN_WORLD = {
    "name": "숨겨진 세계/금지된 구조",
    "category": "hidden_world",
    "desc": "지하·빙하 아래·폐쇄 구역처럼 보이지 않는 곳에 실제로 존재하는 장소와 구조의 비밀.",
    "examples": "빙하 아래 호수, 폐쇄된 지하 시설, 버려진 거대 구조와 금지 구역",
    "visual_fallback": "hidden underground structure",
}
SLOT_CATEGORIES = {
    1: dict(_SCIENCE_MYSTERY),
    2: dict(_HIDDEN_WORLD),
    3: dict(_SCIENCE_MYSTERY),
    4: dict(_HIDDEN_WORLD),
}

_SCIENCE_FOCUS_DOMAINS = (
    {"key": "atmosphere_weather", "name": "대기·기상", "desc": "극한 기상과 설명하기 어려운 대기 관측", "examples": "상층 번개, 원통 구름, 비정상적인 대기파"},
    {"key": "geology_extreme", "name": "지질·극한 자연", "desc": "사막·화산·암석·지각에서 확인된 상식 밖 현상", "examples": "움직이는 돌, 불타는 분화구, 거대 균열"},
    {"key": "earth_records", "name": "지구 기록·탐사 이상", "desc": "지상 탐사와 계측 기록에서 확인된 예상 밖 결과", "examples": "자연 핵반응로, 극한 압력 기록, 오래된 지질 흔적"},
    {"key": "anomalous_physics", "name": "변칙 물리·기술", "desc": "기존 예상과 어긋난 실험·탐사·공학 관측", "examples": "예상 밖 가속, 측정 장비의 반복 이상, 극한 기술 기록"},
)

_HIDDEN_FOCUS_DOMAINS = (
    {"key": "ancient_engineering", "name": "고대 구조·기술", "desc": "실제로 확인된 고대 건축·도시·공학의 숨겨진 구조", "examples": "매몰 도시, 거석 구조, 고대 수로"},
    {"key": "underground_forbidden", "name": "지하·금지 시설", "desc": "지하와 산속에 감춰진 실제 시설·터널·공간", "examples": "폐쇄 지하기지, 암반 터널, 비공개 저장 시설"},
    {"key": "abandoned_restricted", "name": "버려진 장소·제한 구역", "desc": "폐쇄되었거나 접근이 어려운 실제 장소와 남겨진 구조", "examples": "폐광 도시, 버려진 연구소, 출입 제한 터널"},
    {"key": "ice_polar", "name": "빙하·극지", "desc": "빙하와 극지 아래 실제로 보존되거나 발견된 세계", "examples": "빙하 아래 호수, 얼음 터널, 극지 퇴적층"},
)

_OVEREXPOSED_DOMAIN_KEYWORDS = {
    "우주·천문": (
        "우주", "천문", "은하", "블랙홀", "암흑물질", "행성", "금성", "화성",
        "소행성", "혜성", "중력파", "별빛",
    ),
    "바다·심해": (
        "바다", "해저", "심해", "해양", "수중", "열수구", "해구", "대양",
    ),
}


def story_focus_domain(run_id: str) -> dict[str, str] | None:
    """Rotate eight subdomains across two days without changing the four slots."""
    try:
        date_text, slot_text = str(run_id).rsplit("-", 1)
        slot = int(slot_text)
        phase = (datetime.strptime(date_text, "%Y%m%d").toordinal() % 2) * 2
    except (ValueError, TypeError):
        return None
    if slot in (1, 3):
        return dict(_SCIENCE_FOCUS_DOMAINS[phase + (0 if slot == 1 else 1)])
    if slot in (2, 4):
        return dict(_HIDDEN_FOCUS_DOMAINS[phase + (0 if slot == 2 else 1)])
    return None


def _overexposed_recent_domains(recent_topics: list, threshold: int = 2) -> list[str]:
    """Return broad topic domains that appeared too often in recent uploads."""
    haystack = " ".join(str(topic or "") for topic in recent_topics)
    overexposed = []
    for domain, keywords in _OVEREXPOSED_DOMAIN_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits >= threshold:
            overexposed.append(domain)
    return overexposed


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
        columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
        topic_column = "topic" if "topic" in columns else "NULL"
        rows = db.execute(
            f"SELECT title, {topic_column} FROM videos "
            "WHERE date >= ? AND status = 'uploaded' ORDER BY date DESC",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    recent = []
    seen = set()
    for title, topic in rows:
        for value in (title, topic):
            text = str(value or "").strip()
            if text and text not in seen:
                recent.append(text)
                seen.add(text)
    return recent


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
    performance_feedback = (
        build_research_feedback(data_dir)
        if selected == "story"
        else {"winning_patterns": [], "avoid_subjects": [], "evergreen_buckets": []}
    )

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
        "focus_domain": story_focus_domain(run_id) if selected == "story" else None,
        "performance_feedback": performance_feedback,
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

    def _reject_story_duplicate(payload: dict) -> bool:
        if selected != "story":
            return False
        return topic_duplicate_reason(
            payload,
            performance_feedback.get("avoid_subjects") or [],
        ) is not None

    def _validate_candidate(raw_topic: dict) -> dict:
        candidate = validate_topic(raw_topic, selected)
        if selected == "story":
            duplicate_reason = topic_duplicate_reason(
                candidate,
                performance_feedback.get("avoid_subjects") or [],
            )
            if duplicate_reason:
                raise ValueError(duplicate_reason)
        return candidate

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
        topic_dict = _validate_candidate(raw_topic)
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
        allowed_categories = (
            {category["category"]}
            if selected == "story" and category
            else None
        )
        cached = (
            pick_cached(
                data_dir,
                cache_slot,
                recent_topics,
                allowed_categories=allowed_categories,
                reject_payload=(
                    lambda payload: is_rejected_story_topic(payload) or _reject_story_duplicate(payload)
                ) if selected == "story" else None,
            )
            if use_cache else None
        )
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
            topic_dict = _validate_candidate(raw_topic)

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
    focus_domain = context.get("focus_domain") or {}
    feedback = context.get("performance_feedback") or {}
    overexposed_domains = _overexposed_recent_domains(recent)
    category_block = (
        f"- 이번 회차 방향: {category.get('name')}\n"
        f"- 방향 설명: {category.get('desc')}\n"
        f"- 좋은 출발점: {category.get('examples')}"
        if category else
        "- 이번 회차 방향: 위험, 반전, 거대한 규모 중 하나가 분명한 이야기"
    )
    focus_block = (
        f"- 이번 회차 하위 영역: {focus_domain.get('name')}\n"
        f"- 하위 영역 설명: {focus_domain.get('desc')}\n"
        f"- 권장 소재 예시: {focus_domain.get('examples')}\n"
        "- 이 영역 밖의 소재는 선택하지 않는다."
        if focus_domain else
        "- 이번 회차 하위 영역: 상위 방향 안에서 최근 소재와 가장 다른 영역"
    )
    overexposed_block = (
        "- 최근 과다 노출 영역: " + ", ".join(overexposed_domains) + "\n"
        "- 위 영역은 특별히 강한 실물 장면, 위험, 규모, 반전이 없는 한 후보 점수에서 크게 감점한다.\n"
        "- 특히 추상적인 우주·천문 설명과 바다·심해 배경 반복은 피하고, 지상·지하·구조물·탐사 기록 중심으로 바꾼다."
        if overexposed_domains else
        "- 최근 과다 노출 영역: 없음. 그래도 우주·천문과 바다·심해는 강한 실물 장면이 있을 때만 예외적으로 고른다."
    )
    hard_block = (
        "- 추상 우주·천문 소재는 자동 제작 금지: 암흑물질, 블랙홀, 우주론, 은하, 행성 대기, "
        "금성 대기, 타이탄·토성 같은 먼 천체 관측 소재를 선택하지 않는다.\n"
        "- 위 소재는 설명이 흥미로워도 무료 영상이 추상적이고 첫 피드 테스트에서 이탈 위험이 높으므로 후보에서 탈락시킨다.\n"
        "- 대신 지하·빙하·동굴·도시·폐쇄 구역, 사막·화산·호수·실제 구조물처럼 눈앞에 그려지는 지구 기반 소재를 우선한다."
    )
    winners = feedback.get("winning_patterns") or []
    winner_lines = []
    for item in winners[:6]:
        tags = ", ".join(item.get("pattern_tags") or [])
        winner_lines.append(
            f"- 조회 {item.get('views', 0)}회: {item.get('title')} "
            f"(패턴: {tags or '장소·반전·실물 장면'})"
        )
    avoid_subjects = feedback.get("avoid_subjects") or []
    avoid_lines = [f"- {subject}" for subject in avoid_subjects[:40]]
    buckets = feedback.get("evergreen_buckets") or []
    bucket_lines = [f"- {bucket}" for bucket in buckets]
    performance_block = (
        "[성과 기반 추천 방식]\n"
        "- 최근 성과가 좋았던 축은 '지하/숨겨진 장소/고대 공학/숫자/버려진 구조/빙하·화산·호수'처럼 눈에 보이는 실물 미스터리다.\n"
        "- 아래 상위 소재를 그대로 반복하지 말고, 왜 잘됐는지 패턴만 빌려 새 장소·새 사건·새 관측값으로 바꿔라.\n"
        + ("\n".join(winner_lines) if winner_lines else "- 성과 데이터가 부족하면 지하·고대 구조·폐쇄 시설·극한 지형을 우선한다.")
        + "\n\n[강한 중복 회피]\n"
        "- 같은 장소·대상·사건 재포장 금지. 제목만 바꾸거나 숫자만 바꾼 변주는 중복으로 탈락시킨다.\n"
        "- 아래 과거 소재와 핵심 명사 2개 이상이 겹치면 다른 국가·다른 시대·다른 구조·다른 관측값으로 이동한다.\n"
        + ("\n".join(avoid_lines) if avoid_lines else "- 과거 소재 목록 없음")
        + "\n\n[소재 고갈 방지 확장 축]\n"
        "- 소재가 부족하면 회차를 멈추지 말고 아래 축에서 아직 다루지 않은 실물 장소형 소재로 확장한다.\n"
        + ("\n".join(bucket_lines) if bucket_lines else "- 지하·폐쇄시설·고대공학·극한지형·빙하 아래 세계")
    )
    return f"""당신은 '이상한 지구기록' 채널의 한국어 Shorts 리서처다. 검증 가능한 자연·과학·숨겨진 장소·역사 미스터리만 조사한다.

[목표]
- 사람들이 제목만 보고도 "왜? 어떻게?"라고 묻게 되는 소재 1개를 고른다.
- 최종 JSON을 쓰기 전에 서로 다른 후보를 반드시 8개 만든 뒤 내부에서 비교한다.
- 후보 평가 과정은 출력하지 말고 최고점 소재 하나만 JSON으로 출력한다.
- Pexels/Pixabay 무료 스톡에서 실제 대상과 주변 환경을 여러 장면으로 찾을 수 있어야 한다.
- 무료 영상 확보성은 필수 조건이지만 재미와 반전보다 먼저 소재를 결정하지 않는다.
- 최근 사용 소재와 중복하지 않는다: {recent if recent else '없음'}
- 제목 표현이 달라도 최근 소재와 핵심 대상·사건·관측값이 같으면 중복으로 탈락시킨다.

[이번 회차]
{category_block}
{focus_block}
{overexposed_block}
{hard_block}

{performance_block}

[재미 점수 — 후보마다 각 0~5점, 총 30점]
1. 첫 3초 호기심: 설명을 듣기 전에도 결말이 궁금한가?
2. 상식 반전: 대부분의 예상과 실제 답이 다른가?
3. 위험·규모·충격: 거대한 크기, 설명되지 않은 관측, 숨겨진 구조, 역사적 반전 중 하나가 있는가?
4. 남성 시청자 관심: 과학, 기술, 구조, 탐사, 미스터리 본능을 자극하는가?
5. 무료 영상 확보: 실제 대상 또는 같은 사건군의 화면을 여러 장면 구할 수 있는가?
6. 차별성: 최근 소재와 다르고 너무 흔하게 소비된 설명이 아닌가?
- 총점 24점 이상을 목표로 한다. 모든 후보가 24점 미만이어도 추가 호출하거나 회차를 중단하지 말고, 그중 최고점 후보를 반드시 선택한다.
- 최종 JSON의 interest_score에는 최고 후보의 실제 합계를 기록한다.
- selection_reason에는 클릭을 부르는 반전·위험·규모를 한 문장으로 적는다.

[바로 탈락]
- 단순히 색이 다른 이유, 이름의 유래, 평범한 지형 형성 과정만 설명하는 소재
- 제목을 읽은 순간 답이 예상되는 교과서형 질문
- "신비롭다", "놀랍다" 같은 형용사 외에 구체적인 위험·반전·규모가 없는 소재
- 무료 스톡이 많다는 이유만으로 고른 평범한 자연 풍경
- 동물 중심 소재는 선택하지 마라. 동물이 배경에 등장해도 이야기의 주인공이나 핵심 반전으로 삼지 마라.
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

category는 place_nature, science_mystery, hidden_world, history_mystery 중 하나만 사용하라.
"""


def requested_topic_contract_prompt() -> str:
    """Return the grounded story-topic envelope used for a requested topic."""
    return """[응답 JSON 계약]
- 입력 뜻이 둘 이상이면 needs_clarification=true로 두고 interpretations에 서로 구별되는 한국어 해석 2~3개만 쓴다.
- 뜻이 분명하면 needs_clarification=false로 두고 channel_fit과 topic을 쓴다.
- channel_fit=false여도 소재를 거절하지 않는다. 채널 방향 적합성은 경고 정보일 뿐이다.
- 뜻이 분명하면 safety.allowed(boolean)와 safety.reason(구체적인 한국어 판정 이유)을 반드시 쓴다.
- topic은 수동 StoryTopic 계약을 완전히 충족해야 한다. channel_fit=false이면 category에 채널 밖 실제 분류명을 영문 소문자 slug로 보존하고 기존 채널 분류로 위장하지 않는다.
- 최소 2개의 서로 다른 출처를 사용하고, 각 facts 항목에는 claim, value, 해당 사실을 뒷받침하는 실제 기관명 source와 직접 확인 가능한 source_url을 쓴다.
- visual_identity.exact_queries는 실제 대상 이름의 exact: 검색어, safe_fallbacks는 같은 대상군의 무료 스톡 검색어다.
- 검색 그라운딩 성공 경로이므로 verification_method는 grounded_search이며 verified_at을 기록한다.

모호한 경우:
{
  "needs_clarification": true,
  "interpretations": ["첫 번째 구체적 해석", "두 번째 구체적 해석"]
}

명확한 경우:
{
  "needs_clarification": false,
  "channel_fit": false,
  "safety": {
    "allowed": true,
    "reason": "공개된 사실을 설명하는 안전한 소재"
  },
  "topic": {
    "format": "story",
    "topic": "검증된 소재 제목",
    "category": "economy",
    "hook_angle": "구체적인 반전이나 의문",
    "target_keyword": "English subject keyword",
    "core_question": "검증할 핵심 질문",
    "interest_score": 24,
    "selection_reason": "선정 이유",
    "facts": [{
      "claim": "검증된 주장",
      "value": "검증된 설명 또는 수치",
      "source": "첫 번째 공공기관",
      "source_url": "https://first.example.org/direct-source"
    }, {
      "claim": "별도로 검증된 두 번째 주장",
      "value": "두 번째 검증 설명 또는 수치",
      "source": "두 번째 학술기관",
      "source_url": "https://second.example.edu/direct-source"
    }],
    "visual_plan": [{
      "beat": "hook",
      "keywords": ["subject establishing shot", "subject detail"]
    }],
    "visual_identity": {
      "exact_queries": ["exact:verified subject"],
      "safe_fallbacks": ["verified subject environment"],
      "required_exact": true
    },
    "verification_method": "grounded_search",
    "verified_at": "2026-08-10T01:23:45+00:00"
  }
}"""


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
