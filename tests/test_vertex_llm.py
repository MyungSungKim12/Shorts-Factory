from app.services import claude_client


def test_premium_mode_routes_to_vertex_before_legacy_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(claude_client, "paid_features_enabled", lambda data_dir: True)
    monkeypatch.setattr(
        claude_client,
        "_vertex_gemini_call",
        lambda prompt, max_tokens, agent_name, grounded: "premium-result",
    )
    monkeypatch.setattr(
        claude_client,
        "_gemini_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy provider must not run")
        ),
    )

    assert claude_client.call_agent("prompt", grounded=True) == "premium-result"


def test_free_mode_never_calls_vertex_and_keeps_legacy_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(claude_client, "paid_features_enabled", lambda data_dir: False)
    monkeypatch.setattr(
        claude_client,
        "_vertex_gemini_call",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Vertex must not run in free mode")
        ),
    )
    monkeypatch.setattr(
        claude_client, "_gemini_chain", lambda *args, **kwargs: "legacy-result"
    )

    assert claude_client.call_agent("prompt", grounded=True) == "legacy-result"


def test_vertex_request_uses_search_grounding_and_json_for_plain_requests(
    monkeypatch, tmp_path
):
    captured = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
            }

    class Session:
        def __init__(self, credentials):
            pass

        def post(self, url, json, timeout):
            captured.append(json)
            return Response()

    monkeypatch.setattr(claude_client.google.auth, "default", lambda scopes: (object(), None))
    monkeypatch.setattr(claude_client, "AuthorizedSession", Session)
    monkeypatch.setattr(claude_client, "reserve_cost", lambda *args, **kwargs: object())
    monkeypatch.setattr(claude_client, "commit_cost", lambda *args, **kwargs: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-id")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    claude_client._vertex_gemini_call("grounded", 100, "researcher", True)
    claude_client._vertex_gemini_call("plain", 100, "writer", False)

    assert captured[0]["tools"] == [{"googleSearch": {}}]
    assert "responseMimeType" not in captured[0]["generationConfig"]
    assert captured[1]["generationConfig"]["responseMimeType"] == "application/json"
