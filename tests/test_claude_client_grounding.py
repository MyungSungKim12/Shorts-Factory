"""검색 할당량 소진 뒤에도 검증된 JSON을 만드는 폴백 테스트."""

import requests

from app.services import claude_client


def test_grounded_call_falls_back_from_gemini_to_groq_browser_search(monkeypatch):
    monkeypatch.setattr(claude_client, "paid_features_enabled", lambda _data_dir: False)
    monkeypatch.setattr(
        claude_client,
        "_gemini_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("daily quota")),
    )
    monkeypatch.setattr(
        claude_client,
        "_groq_grounded_call",
        lambda prompt, max_tokens, agent_name: '{"topic":"새 소재"}',
        raising=False,
    )

    result = claude_client.call_agent(
        "검증된 소재를 찾아라",
        agent_name="trend-researcher",
        grounded=True,
    )

    assert result == '{"topic":"새 소재"}'


def test_groq_grounded_search_separates_browser_tools_from_json_mode(monkeypatch):
    seen = {}
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": "기관 자료에 따르면 검증됨. https://example.org/source"
                    }
                }]
            }

    def fake_post(url, headers, json, timeout):
        seen["search_body"] = json
        return Response()

    def fake_json_call(prompt, max_tokens, agent_name):
        seen["json_prompt"] = prompt
        return '{"topic":"검증 소재","verification_method":"grounded_search"}'

    monkeypatch.setattr(claude_client.requests, "post", fake_post)
    monkeypatch.setattr(claude_client, "_groq_call", fake_json_call)

    result = claude_client._groq_grounded_call(
        "소재 JSON을 만들어라",
        max_tokens=16000,
        agent_name="trend-researcher",
    )

    assert seen["search_body"]["tools"] == [{"type": "browser_search"}]
    assert "response_format" not in seen["search_body"]
    assert "https://example.org/source" in seen["json_prompt"]
    assert result.startswith('{"topic":"검증 소재"')


def test_groq_grounded_search_retries_transient_rate_limit(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = 0

    class Response:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self.text = "per minute rate limit" if status_code == 429 else ""
            self._body = body or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return self._body

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response(429)
        return Response(
            200,
            {"choices": [{"message": {"content": "verified https://example.org"}}]},
        )

    monkeypatch.setattr(claude_client.requests, "post", fake_post)
    monkeypatch.setattr(claude_client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        claude_client,
        "_groq_call",
        lambda *args, **kwargs: '{"topic":"retry success"}',
    )

    result = claude_client._groq_grounded_call("find fact", 1200, "researcher")

    assert result == '{"topic":"retry success"}'
    assert calls == 2
