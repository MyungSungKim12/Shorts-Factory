"""LLM 공급자 오류가 API 키를 URL과 예외 추적에 노출하지 않는지 검증."""
from app.services import claude_client


class _SuccessResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


def test_gemini_key_is_sent_in_header_not_url(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _SuccessResponse()

    monkeypatch.setattr(claude_client.requests, "post", fake_post)
    result = claude_client._gemini_generate(
        "gemini-flash-latest", "prompt", 100, "secret-key-value", grounded=False
    )
    assert result == "{}"
    assert "secret-key-value" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "secret-key-value"
