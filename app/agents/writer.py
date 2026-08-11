"""대본 작가 에이전트 — script.json 생성."""
import hashlib
import json
from pathlib import Path

from app.content_format import get_content_format
from app.console import safe_print
from app.models import validate_manual_story_topic, validate_script, validate_topic
from app.services.claude_client import call_agent
from app.services.json_extract import extract_json


_PREVIEW_ONLY_FILLER = (
    "알아보겠습니다",
    "살펴보겠습니다",
    "확인해 보겠습니다",
    "파헤쳐 보겠습니다",
)


def ensure_story_information_density(script: dict) -> dict:
    """Reject newly written stories that are long on screen but short on facts."""
    scenes = script.get("scenes") or []
    if not 8 <= len(scenes) <= 10:
        raise ValueError("story requires 8~10 information scenes")
    narration_chars = sum(len(str(scene.get("narration") or "")) for scene in scenes)
    if not 320 <= narration_chars <= 440:
        raise ValueError(f"story narration {narration_chars} chars outside 320~440")
    duration = round(sum(float(scene.get("duration_sec") or 0) for scene in scenes), 1)
    if not 60 <= duration <= 75:
        raise ValueError(f"story duration {duration:.1f}s outside 60~75s")
    roles = {scene.get("role") for scene in scenes}
    if not {"hook", "context", "problem", "mechanism", "payoff", "close"} <= roles:
        raise ValueError("story information roles are incomplete")
    narration = " ".join(str(scene.get("narration") or "") for scene in scenes)
    if any(phrase in narration for phrase in _PREVIEW_ONLY_FILLER):
        raise ValueError("story contains preview-only filler")
    return script


def run_writer(
    data_dir: Path,
    date_str: str,
    content_format: str | None = None,
    work_root: str = "work",
    manual_checked: bool = False,
) -> dict:
    """
    topic.json을 받아 script.json을 생성한다.

    Args:
        data_dir: 데이터 저장 경로
        date_str: YYYYMMDD 형식 날짜

    Returns:
        script.json 스키마 dict
    """
    work_dir = data_dir / work_root / date_str
    topic_file = work_dir / "topic.json"

    if not topic_file.exists():
        raise FileNotFoundError(f"topic.json이 없습니다: {topic_file}")

    # topic.json 로드
    topic = json.loads(topic_file.read_text(encoding="utf-8"))

    selected = get_content_format(content_format)
    if selected == "story":
        topic = (
            validate_manual_story_topic(topic)
            if manual_checked
            else validate_topic(topic, selected)
        )

    # 작가는 Groq 우선 (검색 불필요 + JSON 생성 강점) — Gemini 호출량 절약 겸 부하 분산.
    # 전송 성공이어도 응답 JSON이 잘릴 수 있으므로 검증 실패 시 한 번만 압축 재생성한다.
    base_prompt = _story_writer_prompt(topic) if selected == "story" else _writer_prompt(topic)
    script_dict = None
    last_error = None
    for attempt in range(2):
        prompt = base_prompt
        if attempt:
            prompt += (
                "\n\n[RETRY_JSON_ONLY]\n"
                "이전 응답은 JSON이 불완전하거나 스키마 검증에 실패했다. "
                "설명과 코드펜스를 제외하고 같은 사실만 사용해 더 짧고 완결된 "
                "JSON 객체 하나만 출력하라."
            )
        script_text = call_agent(
            prompt=prompt,
            agent_name="script-writer",
            max_tokens=16000,
            prefer="groq",
        )
        try:
            script_dict = validate_script(extract_json(script_text), selected)
            if selected == "story":
                script_dict = ensure_story_information_density(script_dict)
            script_dict["writer_mode"] = "llm" if attempt == 0 else "llm_retry"
            break
        except ValueError as exc:
            last_error = exc
            if attempt:
                if selected == "story":
                    safe_print("  [script-writer] 모델 응답 2회 실패 → 검증 사실 템플릿으로 전환")
                    script_dict = validate_script(build_verified_story_script(topic), selected)
                    script_dict = ensure_story_information_density(script_dict)
                    script_dict["writer_mode"] = "verified_template"
                    break
                raise
            safe_print("  ⚠️ [script-writer] 불완전한 JSON/스키마 응답 → 압축 JSON으로 1회 재생성")

    if script_dict is None:
        raise RuntimeError("대본 JSON 생성 결과가 없습니다") from last_error

    # script.json 저장 (검증 통과분만 저장됨)
    script_file = work_dir / "script.json"
    script_file.write_text(json.dumps(script_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return script_dict


def build_verified_story_script(topic: dict) -> dict:
    """검증된 topic.json 필드만 조합해 업로드 가능한 스토리 대본을 만든다."""
    facts = topic["facts"]
    visuals = [
        keyword
        for item in topic["visual_plan"]
        for keyword in item["keywords"]
    ]
    unique_visuals = list(dict.fromkeys(visuals))
    if len(unique_visuals) == 1:
        unique_visuals.append(unique_visuals[0])

    roles = [
        "hook", "context", "problem", "mechanism",
        "mechanism", "payoff", "payoff", "close",
    ]
    durations = [7.5] * 8

    def sentence(value: str) -> str:
        normalized = " ".join(str(value).split()).rstrip(" ,.;:!?")
        if len(normalized) > 78:
            shortened = normalized[:54]
            if " " in shortened:
                shortened = shortened.rsplit(" ", 1)[0]
            normalized = shortened.rstrip(" ,.;:!?")
            normalized = f"{normalized}, 관련 기록이 확인됐다"

        return f"{normalized}."

    first = facts[0]
    second = facts[1] if len(facts) > 1 else facts[0]
    narrations = [
        sentence(topic["hook_angle"]),
        sentence(f"{topic['topic']}, 핵심 질문은 {topic['core_question']}"),
        sentence(f"{first['source']} 기록은 {first['claim']}"),
        sentence(f"구체적인 내용은 {first['value']}"),
        sentence(f"{second['source']} 자료는 {second['claim']}"),
        sentence(f"별도로 확인된 내용은 {second['value']}"),
        sentence(topic.get("selection_reason") or topic["core_question"]),
        sentence(f"확인된 기록이 남긴 질문은 {topic['core_question']}"),
    ]
    verified_units = [
        f"{fact['source']}의 공개 자료에는 {fact['claim']}, {fact['value']}"
        for fact in facts
    ]
    unit_index = 0
    while sum(len(item) for item in narrations) < 320 and unit_index < 24:
        index = 1 + (unit_index % 6)
        expanded = narrations[index].rstrip(".") + ", " + verified_units[unit_index % len(verified_units)]
        narrations[index] = sentence(expanded)
        unit_index += 1
    while sum(len(item) for item in narrations) > 440:
        index = max(range(len(narrations)), key=lambda item: len(narrations[item]))
        current = narrations[index].rstrip(".")
        overflow = sum(len(item) for item in narrations) - 440
        keep = max(32, len(current) - overflow)
        shortened = current[:keep].rstrip(" ,.;:!?")
        narrations[index] = f"{shortened}."

    scenes = []
    for index, (role, duration, narration) in enumerate(
        zip(roles, durations, narrations), start=1
    ):
        fact = facts[(index - 1) % len(facts)]
        scenes.append({
            "n": index,
            "role": role,
            "narration": narration,
            "visuals": unique_visuals[:3],
            "duration_sec": duration,
            "emphasis": [fact["claim"]],
        })

    return {
        "format": "story",
        "title": topic["topic"],
        "description": "검증된 자료를 바탕으로 핵심 내용을 정리했습니다.",
        "tags": [topic["category"], topic["target_keyword"]],
        "hook": topic["hook_angle"],
        "scenes": scenes,
        "cta": "이런 이야기의 다음 편도 궁금하다면 구독과 좋아요 부탁드립니다.",
        "total_duration_sec": sum(durations),
    }


def _story_writer_prompt(topic: dict) -> str:
    """검증된 사실만으로 단일 소재 스토리 대본을 만드는 프롬프트."""
    facts = "\n".join(
        f"- {fact['claim']}: {fact['value']} (출처: {fact['source']}, {fact['source_url']})"
        for fact in topic.get("facts", [])
    )
    visual_plan = "\n".join(
        f"- {item['beat']}: {', '.join(item['keywords'])}"
        for item in topic.get("visual_plan", [])
    )
    visual_identity = topic.get("visual_identity") or {}
    narrative_patterns = (
        "모순 공개 → 단서 추적 → 원인 연결 → 의미 회수",
        "결과 선공개 → 시간순 역추적 → 결정적 전환점 → 현재 의미",
        "통념 제시 → 검증 사실로 반박 → 실제 작동 원리 → 한 줄 결론",
        "위험·규모 제시 → 왜 가능한지 질문 → 단계별 원인 → 시청자 관점의 의미",
    )
    pattern_seed = str(topic.get("topic") or topic.get("target_keyword") or "")
    pattern_hash = hashlib.sha256(pattern_seed.encode("utf-8")).hexdigest()
    narrative_pattern = narrative_patterns[int(pattern_hash[:8], 16) % len(narrative_patterns)]
    return f"""당신은 한국어 유튜브 Shorts 스토리 작가다. 하나의 검증된 소재를 설명해 끝까지 보게 만든다. 완성 영상 목표는 70~80초이며 최종 영상은 절대 90초를 넘지 않는다.

[소재]
주제: {topic['topic']}
첫 모순: {topic.get('hook_angle', '')}
핵심 질문: {topic.get('core_question', '')}
검증된 사실:
{facts}
추천 시각 자료:
{visual_plan}
Verified visual_identity:
exact_queries: {', '.join(visual_identity.get('exact_queries', []))}
safe_fallbacks: {', '.join(visual_identity.get('safe_fallbacks', []))}
Preserve exact_queries as the hook and close subject anchor. Use safe_fallbacks only for the same real-world subject family; do not create visual_identity in script JSON.

[차별화 규칙]
- NARRATIVE_PATTERN: {narrative_pattern}
- CHANNEL_EDITORIAL_VIEW: 마지막 payoff 또는 close에 검증된 사실만으로 "왜 이 이야기가 중요한지"를 해석하는 채널 고유 문장 한 개를 넣어라. 새로운 사실·수치·인과관계는 만들지 마라.
- SUBJECT_ANCHORED_VISUALS: 모든 일반 스톡 검색어에도 exact_queries의 실제 대상명 또는 같은 대상군을 식별하는 핵심 명사를 포함하라. 대상과 무관한 분위기 영상, 일몰, 일반 풍경, 캠핑, 도시 영상으로 빈 장면을 채우지 마라.
- 위 NARRATIVE_PATTERN의 순서를 이번 영상의 중심 구조로 사용하고, 제목과 첫 문장을 매번 같은 공식으로 반복하지 마라.

[잔존 구조]
- 8~10개 씬으로 작성하고 duration_sec 합계는 반드시 60~68초다. 앞에 제목 음성 인트로, 뒤에 CTA가 붙으며 최종 영상은 70~85초를 목표로 하고 90초를 넘지 않는다. 전체 음성은 피치를 유지한 채 1.2배로 재생된다.
- 각 narration은 공백 포함 75자 이하, 모든 씬 narration 합계는 공백 포함 320~440자다. 핵심 사실만 남기고 같은 의미를 반복하지 않는다.
- 정보 전달 순서는 배경, 구체적 기록, 기묘한 이유, 가능한 설명 또는 원리, 아직 불확실한 부분, 이 기록이 중요한 이유를 모두 포함한다.
- "알아보겠습니다", "살펴보겠습니다", "확인해 보겠습니다", "파헤쳐 보겠습니다"처럼 내용을 말하지 않고 다음 설명을 예고하는 문장을 쓰지 않는다.
- 모든 narration은 마침표·물음표·느낌표 중 하나의 종결 문장부호로 끝나는 완결 문장이다.
- 전환·대조·조건·원인과 결과의 경계에는 쉼표를 넣고, 문장부호 없이 여러 절을 이어 쓰지 않는다. 소리 내어 읽었을 때 한 호흡이 지나치게 길어지지 않게 한다.
- 0~3초 hook: 인사, 채널명, 로고, 주제 소개 없이 결과나 모순부터 말한다.
- 10초 안에 작은 답 하나를 주되 최종 원리는 남겨 둔다.
- 12~15초, 25~30초, 45~50초 부근에 새 질문, 검증 수치, 시각 전환 중 하나를 둔다.
- 흐름은 hook → context → problem → mechanism → payoff → close다.
- 마지막 close는 첫 문장을 회수하되, close 본문에는 "구독"과 "좋아요"를 절대 넣지 마라. CTA는 별도 cta 필드에만 주제와 자연스럽게 연결된 한 문장으로 쓰고 반드시 "구독"과 "좋아요"를 모두 포함한다.
- 검증된 사실 이외의 수치, 인과관계, 고유명사를 만들지 않는다.

[화면 규칙]
- 각 씬 visuals는 무료 Pexels/Pixabay에서 찾을 수 있는 구체적인 영어 검색어 2~3개다.
- 희귀 장소·고유 구조물의 실제 모습이 필요한 검색어는 `exact: Blood Falls Antarctica`처럼 exact: 접두사를 붙인다. 이 검색어는 허용 라이선스와 저작자 정보를 기록하는 Wikimedia Commons 이미지를 우선한다.
- visuals에는 추상어만 쓰지 말고 장소, 지형, 구조물, 동물 같은 실제 대상을 쓴다.
- narration은 자연스럽게 이어지는 짧은 한국어 1~2문장이다.
- emphasis는 화면에서 강조할 짧은 핵심어 또는 숫자 0~4개다.

[JSON만 출력]
{{
  "format": "story",
  "title": "10분만 머물러도 위험한 지하 수정 동굴의 비밀",
  "description": "검증 내용과 출처를 요약한 설명",
  "tags": ["태그1", "태그2", "태그3"],
  "hook": "첫 3초 문장",
  "scenes": [
    {{
      "n": 1,
      "role": "hook",
      "narration": "결과 또는 모순을 먼저 말하는 문장",
      "visuals": ["desert lake aerial", "cracked desert ground"],
      "duration_sec": 7.5,
      "emphasis": ["비가 없는데", "마르지 않는다"]
    }}
  ],
  "cta": "이런 자연의 비밀이 더 궁금하다면, 구독과 좋아요 부탁드립니다.",
  "total_duration_sec": 60
}}

허용 role은 hook, context, problem, mechanism, payoff, close뿐이다. 첫 씬은 hook, 마지막 씬은 close로 하고 씬 번호를 1부터 연속으로 매겨라.
"""


def _writer_prompt(topic: dict) -> str:
    """작가 에이전트의 고효율 프롬프트."""
    items_str = "\n".join([
        f"  {i['rank']}위: {i['name']} (수치: {i['fact']}, 출처: {i['source']})"
        for i in topic.get("items", [])
    ])

    return f"""당신은 랭킹 숏츠 전문 대본 작가다. 시청자가 1위를 확인하기 전에 이탈하지 않게 만드는 것이 유일한 목표다.

⚠️ 중요: 모든 텍스트는 반드시 한국어로 작성할 것. 영어 사용 금지.
⚠️ 표기 규칙: 순위와 수치는 반드시 아라비아 숫자로 쓸 것 — "1위", "2위", "979m", "350만".
   "일위", "이위", "구백칠십구미터" 같은 한글 숫자 표기는 절대 금지 (자막 가독성 때문).

[소재 + 순위 데이터]
주제: {topic['topic']}
1위 의외성 포인트: {topic.get('hook_angle', '')}
순위 데이터:
{items_str}

[대본 규칙]
1. hook(첫 1~2초가 조회수를 좌우): 첫 씬은 전체화면에 큰 문구로 나가므로, 짧고 강한 반전이어야 한다.
   공식: [사람들이 아는 대상] + [상식 뒤집기] + [구체적 수치] + [정답 미공개]
   - 나쁜 예: "세계에서 가장 큰 사막 TOP 5. 1위는 의외입니다" (뻔함)
   - 좋은 예: "사하라는 1위가 아닙니다. 진짜 1위는 사하라보다 훨씬 큽니다"
             "나이아가라보다 19배 높은 폭포가 있습니다"
             "한반도의 10배였던 제국, 그런데도 1위가 아닙니다"
   - 첫 문장에 '작은 정답 하나'를 먼저 줘도 좋다(낚시 아님 신뢰): "사하라는 사실 3위입니다"
   - 단, 최종 1위의 정답 자체는 hook에서 밝히지 말 것. 수치는 topic.json 검증값만 사용.
   - hook narration은 **짧고 굵게**: 한국어 30자 이내(공백 포함), 1~2문장. 전체화면 큰 글자로 통째 표시되므로
     길면 글자가 작아진다. "이보다/그것보다" 같은 지시어로 늘이지 말고 핵심만.
     예: "대피라미드? 사실 4위입니다. 1위는 7천년 더 오래됐죠" (짧고 임팩트)
2. 카운트다운: 반드시 {topic['ranking_size']}위부터 1위로 내려가는 역순.
3. 씬별 목표 길이 (템포가 완주율을 좌우한다):
   - hook: 1~2초 (짧게 훅킹)
   - 하위 순위(5~2위): 각 4초 전후 (한 씬 = 순위+이름+수치 하나, 부연 금지)
   - 1위 직전 긴장 씬: 1초 미만
   - 1위: 6~7초 (의외성 + 수치)
   - CTA: 1~2초
4. 1위 직전에 "그리고 1위는..." 형태의 긴장 씬(1초 미만)을 별도로 넣어라.
5. fact의 수치를 반드시 narration에 포함하라. narration은 짧게 — 한 씬당 한국어 2문장 이내.
6. cta: 댓글 유도형 ("여러분의 1위는?")
7. 총 35~50초 목표 (절대 55초를 넘기지 말 것 — TTS 실제 길이가 계획보다 길어지므로 여유를 두라).
   각 narration을 소리내 읽었을 때의 실제 길이를 기준으로 duration_sec를 잡아라.
8. rank는 순위 공개 씬에만 넣어라. hook·긴장 씬·CTA처럼 순위가 아닌 씬은 rank를 null로 하라 (0 금지).

[메타데이터 규칙]
- title: "TOP {topic['ranking_size']}" + 1위 궁금증 유발. 1위는 제목에 공개하지 말 것.
- visual: 무료 스톡에서 검색될 **짧고 구체적인 영어 2~3단어**. 문장 금지.
  · 순위 씬은 해당 항목의 대상을 그대로 (예: "greyhound running", "eiffel tower paris").
  · hook·긴장·CTA 씬은 추상어("dramatic transition", "cute happy dog asking") 금지.
    대신 이 영상 주제의 대표 대상을 넣어라 (예: 개 주제면 "dog running", 도시 주제면 "city skyline").
  · 특정 국가·인물명이 검색어 첫 단어로 오지 않게 하라 ("afghan hound" 대신 "dog running").

[검증 — 통과 후 JSON만 출력]
□ 순위가 역순인가?      □ 1위 직전 긴장 씬이 있는가?
□ 모든 순위 narration에 수치가 있는가?  □ 제목에 1위가 노출되지 않았는가?
□ 씬 duration 합계 = total_duration_sec인가?

[JSON 스키마]
{{
  "title": "제목",
  "description": "설명란 (2~3문장 + 해시태그 3개)",
  "tags": ["태그1", "태그2", "태그3"],
  "hook": "0~2초 첫 문장",
  "scenes": [
    {{"n": 1, "rank": {topic['ranking_size']}, "narration": "나레이션", "visual": "english keyword", "duration_sec": 5}},
    {{"n": 2, "rank": {topic['ranking_size']-1}, "narration": "...", "visual": "...", "duration_sec": 5}}
  ],
  "cta": "마지막 행동 유도",
  "total_duration_sec": 48
}}
"""
