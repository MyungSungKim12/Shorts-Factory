"""대본 작가 에이전트 — script.json 생성."""
import json
from pathlib import Path

from app.services.claude_client import call_agent


def run_writer(data_dir: Path, date_str: str) -> dict:
    """
    topic.json을 받아 script.json을 생성한다.

    Args:
        data_dir: 데이터 저장 경로
        date_str: YYYYMMDD 형식 날짜

    Returns:
        script.json 스키마 dict
    """
    work_dir = data_dir / "work" / date_str
    topic_file = work_dir / "topic.json"

    if not topic_file.exists():
        raise FileNotFoundError(f"topic.json이 없습니다: {topic_file}")

    # topic.json 로드
    topic = json.loads(topic_file.read_text(encoding="utf-8"))

    # Claude를 통해 작가 에이전트 실행
    # 작가는 Groq 우선 (검색 불필요 + JSON 생성 강점) — Gemini 호출량 절약 겸 부하 분산
    script_text = call_agent(
        prompt=_writer_prompt(topic),
        agent_name="script-writer",
        max_tokens=16000,
        prefer="groq",
    )

    # JSON 파싱 (견고 추출 — 코드펜스·후행 텍스트 제거)
    from app.services.json_extract import extract_json
    script_dict = extract_json(script_text)

    # 검증 게이트 — 역순 구조/길이 정합성/제목 길이 검사. 실패 시 파이프라인 중단.
    from app.models import validate_script
    script_dict = validate_script(script_dict)

    # script.json 저장 (검증 통과분만 저장됨)
    script_file = work_dir / "script.json"
    script_file.write_text(json.dumps(script_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return script_dict


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
