from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import requests


def _configured_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")


def test_missing_credentials_is_disabled_without_http(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
    )

    assert send_alert(tmp_path, "upload:1", "ok") == {"status": "disabled"}


def test_http_failure_never_raises(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    _configured_env(monkeypatch)

    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("telegram unavailable")

    monkeypatch.setattr(requests, "post", raise_timeout)

    assert send_alert(tmp_path, "upload:1", "ok")["status"] == "error"


def test_unexpected_http_adapter_failure_never_raises(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    _configured_env(monkeypatch)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("adapter failed")),
    )

    assert send_alert(tmp_path, "upload:1", "ok")["status"] == "error"


def test_duplicate_event_is_sent_once(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    _configured_env(monkeypatch)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)

    assert send_alert(tmp_path, "upload:20260721-2:success", "first") == {"status": "sent"}
    result = send_alert(tmp_path, "upload:20260721-2:success", "second")

    assert result == {"status": "duplicate"}
    assert len(calls) == 1
    assert calls[0][1] == {
        "data": {"chat_id": "-1001234567890", "text": "first"},
        "timeout": (5, 10),
    }


def test_dedupe_expires_after_twenty_four_hours(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    _configured_env(monkeypatch)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: type(
            "Response",
            (), {"raise_for_status": lambda self: None, "json": lambda self: {"ok": True}},
        )(),
    )
    initial = datetime(2026, 7, 21, tzinfo=timezone.utc)

    assert send_alert(tmp_path, "cache:daily", "first", now=initial)["status"] == "sent"
    assert send_alert(
        tmp_path, "cache:daily", "second", now=initial + timedelta(hours=24, seconds=1)
    )["status"] == "sent"


def test_safe_error_redacts_bot_token_and_truncates():
    from app.services.notifications import safe_error

    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    message = safe_error(RuntimeError(f"request failed for {token}: " + "x" * 400))

    assert token not in message
    assert "[redacted]" in message
    assert len(message) == 300


def test_safe_error_redacts_token_embedded_in_bot_api_url():
    from app.services.notifications import safe_error

    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    message = safe_error(
        RuntimeError(f"POST https://api.telegram.org/bot{token}/sendMessage failed")
    )

    assert token not in message
    assert "bot[redacted]" in message


def test_corrupt_state_lock_cleanup_never_raises(tmp_path):
    from app.services.notifications import _release_state_lock

    state_path = tmp_path / "notifications" / "state.json"
    state_path.parent.mkdir()
    state_path.with_name(".state.json.lock").write_bytes(b"\xff")

    _release_state_lock(state_path, "owner-token")


def test_state_failure_never_reaches_http_or_raises(tmp_path, monkeypatch):
    from app.services import notifications

    _configured_env(monkeypatch)
    monkeypatch.setattr(
        notifications,
        "_read_state",
        lambda path: (_ for _ in ()).throw(OSError("state unavailable")),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not run")),
    )

    assert notifications.send_alert(tmp_path, "upload:1", "ok")["status"] == "error"


def test_concurrent_duplicate_event_posts_only_once(tmp_path, monkeypatch):
    from app.services import notifications

    _configured_env(monkeypatch)
    first_post_started = Event()
    release_post = Event()
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(*args, **kwargs):
        calls.append(True)
        first_post_started.set()
        release_post.wait(timeout=2)
        return Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    results = []
    first = Thread(
        target=lambda: results.append(notifications.send_alert(tmp_path, "upload:race", "ok"))
    )
    second = Thread(
        target=lambda: results.append(notifications.send_alert(tmp_path, "upload:race", "ok"))
    )

    first.start()
    assert first_post_started.wait(timeout=1)
    second.start()
    second.join(timeout=0.2)
    release_post.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(calls) == 1
    assert sorted(result["status"] for result in results) == ["duplicate", "sent"]
