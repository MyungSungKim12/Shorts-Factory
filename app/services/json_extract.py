"""LLM 응답에서 JSON 객체를 견고하게 추출.

그라운딩(검색) 모드는 JSON 강제가 안 돼서, 모델이 다음처럼 응답할 수 있다:
- ```json ... ``` 마크다운 펜스로 감싸기
- JSON 뒤에 인용·출처·설명 텍스트를 덧붙이기 (→ json.loads가 "Extra data" 오류)
- JSON 앞에 "다음은 결과입니다:" 같은 머리말

단순 json.loads나 greedy 정규식으로는 위를 못 거른다.
이 함수는 첫 번째 '중괄호 균형이 맞는' 완전한 JSON 객체만 잘라내 파싱한다.
"""
import json


def extract_json(text: str) -> dict:
    """텍스트에서 첫 번째 완전한 JSON 객체를 추출해 dict로 반환. 실패 시 ValueError."""
    if not text or not text.strip():
        raise ValueError("빈 응답")

    s = text.strip()

    # 1) 마크다운 코드펜스 제거
    if "```" in s:
        # ```json 또는 ``` 이후 ~ 다음 ``` 사이를 우선 시도
        fence = s.split("```")
        for part in fence:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                s = p
                break

    # 2) 그대로 파싱 시도
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 3) 중괄호 균형 맞추며 첫 완전한 객체 잘라내기 (문자열 내부 중괄호/이스케이프 처리)
    start = s.find("{")
    if start == -1:
        raise ValueError(f"JSON 객체를 찾을 수 없음:\n{text[:300]}")

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start:i + 1]
                return json.loads(candidate)  # 여기서 실패하면 진짜 불량 → 예외 전파

    raise ValueError(f"JSON 객체가 완결되지 않음:\n{text[:300]}")
