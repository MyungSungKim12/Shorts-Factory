"""대본 작가 에이전트 — script.json 생성."""
import json
from pathlib import Path

from app.content_format import get_content_format
from app.services.claude_client import call_agent


def run_writer(
    data_dir: Path,
    date_str: str,
    content_format: str | None = None,
    work_root: str = "work",
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

    # Claude를 통해 작가 에이전트 실행
    # 작가는 Groq 우선 (검색 불필요 + JSON 생성 강점) — Gemini 호출량 절약 겸 부하 분산
    script_text = call_agent(
        prompt=_story_writer_prompt(topic) if selected == "story" else _writer_prompt(topic),
        agent_name="script-writer",
        max_tokens=16000,
        prefer="groq",
    )

    # JSON 파싱 (견고 추출 — 코드펜스·후행 텍스트 제거)
    from app.services.json_extract import extract_json
    script_dict = extract_json(script_text)

    # 검증 게이트 — 역순 구조/길이 정합성/제목 길이 검사. 실패 시 파이프라인 중단.
    from app.models import validate_script
    script_dict = validate_script(script_dict, selected)

    # script.json 저장 (검증 통과분만 저장됨)
    script_file = work_dir / "script.json"
    script_file.write_text(json.dumps(script_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return script_dict


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
    return f"""당신은 한국어 유튜브 Shorts 스토리 작가다. 하나의 검증된 소재를 60~75초 동안 설명해 끝까지 보게 만든다.

[소재]
주제: {topic['topic']}
첫 모순: {topic.get('hook_angle', '')}
핵심 질문: {topic.get('core_question', '')}
검증된 사실:
{facts}
추천 시각 자료:
{visual_plan}

[잔존 구조]
- 7~10개 씬으로 작성하고 duration_sec 합계는 반드시 60~75초다.
- 0~3초 hook: 인사, 채널명, 로고, 주제 소개 없이 결과나 모순부터 말한다.
- 10초 안에 작은 답 하나를 주되 최종 원리는 남겨 둔다.
- 12~15초, 25~30초, 45~50초 부근에 새 질문, 검증 수치, 시각 전환 중 하나를 둔다.
- 흐름은 hook → context → problem → mechanism → payoff → close다.
- 마지막 close는 첫 문장을 회수하며 CTA는 필요할 때만 한 문장으로 쓴다.
- 검증된 사실 이외의 수치, 인과관계, 고유명사를 만들지 않는다.

[화면 규칙]
- 각 씬 visuals는 무료 Pexels/Pixabay에서 찾을 수 있는 구체적인 영어 검색어 2~3개다.
- visuals에는 추상어만 쓰지 말고 장소, 지형, 구조물, 동물 같은 실제 대상을 쓴다.
- narration은 자연스럽게 이어지는 한국어 1~3문장이다.
- emphasis는 화면에서 강조할 짧은 핵심어 또는 숫자 0~4개다.

[JSON만 출력]
{{
  "format": "story",
  "title": "100자 이하 제목",
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
  "cta": "",
  "total_duration_sec": 66
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
