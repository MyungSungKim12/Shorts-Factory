"""LLM 에이전트 호출 서비스 (Gemini 무료 API)."""
import os
import time

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# 기본 모델이 혼잡(503)이거나 할당량 초과(429)일 때 순서대로 시도할 예비 모델
# (키로 사용 가능한 모델 확인: /v1beta/models?key=... 목록 기준)
FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
]


def call_agent(prompt: str, agent_name: str = "general", max_tokens: int = 16000) -> str:
    """
    Gemini를 통해 에이전트를 호출한다. JSON 강제 모드 사용.
    503/429 발생 시 재시도 후 예비 모델로 자동 전환한다.

    Args:
        prompt: 에이전트에게 전달할 프롬프트
        agent_name: 에이전트 이름 (로깅용)
        max_tokens: 최대 응답 토큰 수

    Returns:
        모델의 응답 텍스트 (JSON 문자열)
    """
    api_key = os.getenv("GEMINI_API_KEY")
    primary_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY가 없습니다. https://aistudio.google.com/app/apikey 에서 "
            "무료 키를 발급받아 .env에 넣으세요."
        )

    # 기본 모델 + 예비 모델 (중복 제거, 순서 유지)
    models = [primary_model] + [m for m in FALLBACK_MODELS if m != primary_model]

    last_error = "알 수 없는 오류"
    for model in models:
        # 모델당 2회 시도 (일시적 혼잡 대비)
        for attempt in range(2):
            try:
                return _generate(model, prompt, max_tokens, api_key)
            except _SkipModelError as e:
                # 이 모델은 사용 불가 — 재시도 없이 다음 모델로
                last_error = str(e)
                print(f"  ⚠️ [{agent_name}] {model} 사용 불가({e}) → 다음 모델로")
                break
            except _RetryableError as e:
                last_error = str(e)
                print(f"  ⚠️ [{agent_name}] {model} {attempt + 1}차 실패: {e}")
                if attempt == 0:
                    time.sleep(10)  # 같은 모델 재시도 전 대기
        else:
            print(f"  → 예비 모델로 전환 시도...")

    raise RuntimeError(f"모든 Gemini 모델 호출 실패. 마지막 오류: {last_error}")


class _RetryableError(Exception):
    """재시도/모델 전환으로 해결 가능한 일시적 오류."""


class _SkipModelError(Exception):
    """모델 자체가 사용 불가(404 등) — 재시도 없이 다음 모델로."""


def _generate(model: str, prompt: str, max_tokens: int, api_key: str) -> str:
    """단일 모델로 1회 생성 요청."""
    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
            # JSON 강제 모드 — 모델이 유효한 JSON 외의 텍스트를 출력할 수 없음
            "response_mime_type": "application/json",
        },
    }

    try:
        response = requests.post(url, json=body, timeout=120)
        response.raise_for_status()

        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini 응답에 결과 없음 (차단됐을 수 있음): {result}")

        # 출력이 토큰 한도로 잘렸으면 불량 JSON이므로 재시도/모델 전환
        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise _RetryableError("출력이 토큰 한도로 잘림")

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()

        if not text:
            raise RuntimeError(f"Gemini 응답이 비어 있음: {result}")

        return text

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        detail = e.response.text[:300] if e.response is not None else str(e)
        if status in (429, 500, 503):
            # 혼잡/할당량 — 재시도 또는 예비 모델로 해결 가능
            raise _RetryableError(f"HTTP {status} (혼잡/할당량)")
        if status == 404:
            # 모델 제공 종료 등 — 이 모델은 건너뛰고 다음 모델로
            raise _SkipModelError("HTTP 404 (모델 사용 불가)")
        raise RuntimeError(f"Gemini API 오류 (HTTP {status}): {detail}")
    except requests.ConnectionError:
        raise _RetryableError("네트워크 연결 실패")
    except requests.Timeout:
        raise _RetryableError("응답 시간 초과")
