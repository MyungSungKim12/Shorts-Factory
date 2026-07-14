"""LLM 에이전트 호출 서비스 — 다중 제공자 (Gemini 주력 + Groq 폴백).

제공자 순서:
- grounded(검색 기반):  Gemini 그라운딩 → Groq compound(내장 웹검색)
- 일반, prefer="groq":  Groq gpt-oss → Gemini        (작가용 — Gemini 호출 절약)
- 일반, prefer="gemini": Gemini → Groq
"""
import os
import time

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Gemini: 기본 모델이 혼잡(503)이거나 할당량 초과(429)일 때 순서대로 시도할 예비 모델
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
]


# 일시적 오류(분당 한도/혼잡) 재시도 간격: 30초 → 60초 → 120초
_BACKOFF_SECONDS = [30, 60, 120]


class _RetryableError(Exception):
    """재시도/제공자 전환으로 해결 가능한 일시적 오류 (분당 429/5xx/네트워크)."""


class _SkipModelError(Exception):
    """모델 자체가 사용 불가(404 등) — 재시도 없이 다음 모델로."""


class _DailyQuotaError(Exception):
    """일일 한도 초과 — 재시도 무의미, 즉시 다음 제공자로 전환."""


def _is_daily_quota(detail: str) -> bool:
    """429 응답 본문으로 일일 한도(재시도 무의미)와 분당 한도(재시도 유효)를 구분."""
    t = detail.lower().replace(" ", "")
    return any(k in t for k in ("perday", "daily", "tpd", "rpd"))


def call_agent(
    prompt: str,
    agent_name: str = "general",
    max_tokens: int = 16000,
    grounded: bool = False,
    prefer: str = "gemini",
) -> str:
    """
    LLM 호출 — 제공자/모델 자동 폴백.

    Args:
        prompt: 에이전트에게 전달할 프롬프트
        agent_name: 에이전트 이름 (로깅용)
        max_tokens: 최대 응답 토큰 수
        grounded: True면 Gemini 검색 그라운딩 시도 (할당량 있을 때만 성공, 없으면 상위에서 보수 모드로 폴백)
        prefer: 일반 호출 시 우선 제공자 ("gemini" | "groq")

    Returns:
        모델의 응답 텍스트 (JSON 문자열 또는 JSON 포함 텍스트)
    """
    if grounded:
        # 검색 경로는 Gemini 그라운딩만 (Groq compound는 무료 티어에서 413으로 상시 실패해 제거).
        # 그라운딩 할당량이 소진되면 이 호출은 실패하고, 리서처가 보수 모드로 폴백한다.
        providers = [
            ("gemini(검색)", lambda: _gemini_chain(prompt, max_tokens, agent_name, grounded=True)),
        ]
    elif prefer == "groq":
        providers = [
            ("groq", lambda: _groq_call(prompt, max_tokens, agent_name)),
            ("gemini", lambda: _gemini_chain(prompt, max_tokens, agent_name)),
        ]
    else:
        providers = [
            ("gemini", lambda: _gemini_chain(prompt, max_tokens, agent_name)),
            ("groq", lambda: _groq_call(prompt, max_tokens, agent_name)),
        ]

    errors = []
    for name, fn in providers:
        try:
            return fn()
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"  ⚠️ [{agent_name}] 제공자 {name} 실패 → 다음 제공자로")

    raise RuntimeError(f"모든 LLM 제공자 실패: {' | '.join(errors)}")


# ---------------------------------------------------------------- Gemini

def _gemini_chain(prompt: str, max_tokens: int, agent_name: str, grounded: bool = False) -> str:
    """Gemini 호출 — 기본 모델 + 예비 모델 순차 시도."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 미설정")

    primary = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    models = [primary] + [m for m in GEMINI_FALLBACK_MODELS if m != primary]

    last_error = "알 수 없는 오류"
    for model in models:
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            try:
                return _gemini_generate(model, prompt, max_tokens, api_key, grounded)
            except _SkipModelError as e:
                last_error = str(e)
                print(f"  ⚠️ [{agent_name}] {model} 사용 불가({e}) → 다음 모델로")
                break
            except _DailyQuotaError as e:
                # 일일 한도는 오늘 안에 안 풀림 — Gemini 전체 포기, 즉시 다음 제공자로
                print(f"  ⚠️ [{agent_name}] {model} 일일 한도 초과 → Gemini 중단, 제공자 전환")
                raise RuntimeError(f"Gemini 일일 한도 초과: {e}")
            except _RetryableError as e:
                last_error = str(e)
                print(f"  ⚠️ [{agent_name}] {model} {attempt + 1}차 실패: {e}")
                if attempt < len(_BACKOFF_SECONDS):
                    time.sleep(_BACKOFF_SECONDS[attempt])

    raise RuntimeError(f"Gemini 전 모델 실패 (마지막: {last_error})")


def _gemini_generate(model: str, prompt: str, max_tokens: int, api_key: str, grounded: bool) -> str:
    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"

    generation_config = {"maxOutputTokens": max_tokens, "temperature": 0.7}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    if grounded:
        # Google 검색 그라운딩 (JSON 강제 모드와 병용 불가)
        body["tools"] = [{"google_search": {}}]
    else:
        generation_config["response_mime_type"] = "application/json"

    try:
        response = requests.post(url, json=body, timeout=120)
        response.raise_for_status()

        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini 응답에 결과 없음 (차단됐을 수 있음): {result}")

        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise _RetryableError("출력이 토큰 한도로 잘림")

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise RuntimeError(f"Gemini 응답이 비어 있음: {result}")
        return text

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        detail = e.response.text[:500] if e.response is not None else str(e)
        if status == 429:
            # 그라운딩(검색) 429는 혼잡이 아니라 할당량 문제 — 재시도 낭비 없이 즉시 제공자 전환
            if grounded or _is_daily_quota(detail):
                raise _DailyQuotaError("할당량 소진 (재시도 무의미)")
            raise _RetryableError("HTTP 429 (분당 한도/혼잡)")
        if status in (500, 503):
            raise _RetryableError(f"HTTP {status} (서버 혼잡)")
        if status == 404:
            raise _SkipModelError("HTTP 404 (모델 사용 불가)")
        raise RuntimeError(f"Gemini API 오류 (HTTP {status}): {detail[:300]}")
    except requests.ConnectionError:
        raise _RetryableError("네트워크 연결 실패")
    except requests.Timeout:
        raise _RetryableError("응답 시간 초과")


# ---------------------------------------------------------------- Groq

def _groq_call(prompt: str, max_tokens: int, agent_name: str) -> str:
    """Groq 호출 (OpenAI 호환 API). 무료 티어: gpt-oss-120b 일 1,000회/20만 토큰."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY 미설정 — console.groq.com에서 무료 발급")

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    json_mode = True

    last_error = "알 수 없는 오류"
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            return _groq_generate(model, prompt, max_tokens, api_key, json_mode)
        except _DailyQuotaError as e:
            print(f"  ⚠️ [{agent_name}] groq/{model} 일일 한도 초과 → 제공자 전환")
            raise RuntimeError(f"Groq 일일 한도 초과: {e}")
        except _RetryableError as e:
            last_error = str(e)
            print(f"  ⚠️ [{agent_name}] groq/{model} {attempt + 1}차 실패: {e}")
            if attempt < len(_BACKOFF_SECONDS):
                time.sleep(_BACKOFF_SECONDS[attempt])

    raise RuntimeError(f"Groq 실패 (마지막: {last_error})")


def _groq_generate(model: str, prompt: str, max_tokens: int, api_key: str, json_mode: bool) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        # Groq 모델들의 max_tokens 상한은 8192 — 초과 시 400 에러
        "max_tokens": min(max_tokens, 8192),
        "temperature": 0.7,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=120,
        )
        if response.status_code == 400 and json_mode:
            # 일부 모델은 json_object 미지원 — 일반 모드로 1회 재시도
            body.pop("response_format")
            response = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=120,
            )
        response.raise_for_status()

        result = response.json()
        text = (result.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError(f"Groq 응답이 비어 있음: {str(result)[:200]}")
        return text

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        detail = e.response.text[:500] if e.response is not None else str(e)
        if status == 429:
            if _is_daily_quota(detail):
                raise _DailyQuotaError("일일 할당량 소진")
            raise _RetryableError("HTTP 429 (분당 한도/혼잡)")
        if status in (500, 503):
            raise _RetryableError(f"HTTP {status} (서버 혼잡)")
        raise RuntimeError(f"Groq API 오류 (HTTP {status}): {detail[:300]}")
    except requests.ConnectionError:
        raise _RetryableError("네트워크 연결 실패")
    except requests.Timeout:
        raise _RetryableError("응답 시간 초과")