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

    # JSON 파싱
    try:
        script_dict = json.loads(script_text)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{.*\}', script_text, re.DOTALL)
        if match:
            script_dict = json.loads(match.group())
        else:
            raise ValueError(f"작가 응답을 JSON으로 파싱할 수 없음:\n{script_text}")

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
1. hook(첫 1초 승부): 1위의 '결론'을 미리 던져 궁금증을 폭발시켜라. 5위부터 나열 금지.
   - 나쁜 예: "오늘의 5위는 ~입니다"
   - 좋은 예: "절대 가면 안 되는 여행지 1위, 당신 예상과 완전히 다릅니다" / "이 동물이 1위인 거 아무도 몰랐을걸요?"
   - 즉 "결론(1위)이 궁금하지? 그럼 5위부터 보자" 구조. 단 1위의 정답 자체는 hook에서 밝히지 말 것.
2. 카운트다운: 반드시 {topic['ranking_size']}위부터 1위로 내려가는 역순.
3. 하위 순위(5~2위)는 씬당 4~6초로 속도감 있게, 1위는 6~8초로 여유 있게.
4. 1위 직전에 "그리고 1위는..." 형태의 긴장 씬(1초 내외)을 별도로 넣어라.
5. fact의 수치를 반드시 narration에 포함하라.
6. cta: 댓글 유도형 ("여러분의 1위는?")
7. 총 30~55초.
8. rank는 순위 공개 씬에만 넣어라. hook·긴장 씬·CTA처럼 순위가 아닌 씬은 rank를 null로 하라 (0 금지).

[메타데이터 규칙]
- title: "TOP {topic['ranking_size']}" + 1위 궁금증 유발. 1위는 제목에 공개하지 말 것.
- visual은 영어로 (무료 스톡 검색용)

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
