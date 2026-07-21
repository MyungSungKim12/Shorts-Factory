from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import requests


def _token():
    return "123456789" + ":" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcdefghi"


def _configured_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALERTS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _token())
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


def test_alerts_default_disabled_even_with_credentials(tmp_path, monkeypatch):
    from app.services.notifications import send_alert

    monkeypatch.delenv("TELEGRAM_ALERTS_ENABLED", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _token())
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
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

    token = _token()
    message = safe_error(RuntimeError(f"request failed for {token}: " + "x" * 400))

    assert token not in message
    assert "[redacted]" in message
    assert len(message) == 300


def test_safe_error_redacts_token_embedded_in_bot_api_url():
    from app.services.notifications import safe_error

    token = _token()
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


def test_stale_notification_lock_is_recovered(tmp_path, monkeypatch):
    from app.services import notifications

    _configured_env(monkeypatch)
    state_path = tmp_path / "notifications" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.with_name(".state.json.lock").write_text(
        '{"token":"old","pid":999999,"event_key":"old:event",'
        '"started_at":"2026-07-20T00:00:00+00:00"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        notifications.requests,
        "post",
        lambda *args, **kwargs: type(
            "Response",
            (), {"raise_for_status": lambda self: None, "json": lambda self: {"ok": True}},
        )(),
    )

    result = notifications.send_alert(
        tmp_path,
        "new:event",
        "ok",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert result == {"status": "sent"}


def test_stale_partially_written_lock_is_recovered(tmp_path, monkeypatch):
    from app.services import notifications

    _configured_env(monkeypatch)
    state_path = tmp_path / "notifications" / "state.json"
    state_path.parent.mkdir(parents=True)
    lock_path = state_path.with_name(".state.json.lock")
    lock_path.write_text("{", encoding="utf-8")
    stale_epoch = datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp()
    notifications.os.utime(lock_path, (stale_epoch, stale_epoch))
    monkeypatch.setattr(
        notifications.requests,
        "post",
        lambda *args, **kwargs: type(
            "Response",
            (), {"raise_for_status": lambda self: None, "json": lambda self: {"ok": True}},
        )(),
    )

    result = notifications.send_alert(
        tmp_path,
        "new:event",
        "ok",
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert result == {"status": "sent"}


def test_stale_lock_snapshot_does_not_unlink_replaced_owner(tmp_path):
    from app.services import notifications

    lock_path = tmp_path / ".state.json.lock"
    lock_path.write_text("old-owner", encoding="utf-8")
    snapshot = notifications._read_lock_snapshot(lock_path)
    lock_path.write_text("new-owner", encoding="utf-8")

    assert notifications._unlink_lock_if_unchanged(lock_path, snapshot) is False
    assert lock_path.read_text(encoding="utf-8") == "new-owner"


def test_current_process_liveness_check_never_sends_a_signal(monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(
        notifications.os,
        "kill",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("liveness checks must not signal the current process")
        ),
    )

    assert notifications._pid_alive(notifications.os.getpid()) is True


def test_windows_liveness_check_uses_non_signalling_probe(monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications.os, "name", "nt")
    monkeypatch.setattr(notifications, "_windows_pid_alive", lambda pid: pid == 4242, raising=False)
    monkeypatch.setattr(
        notifications.os,
        "kill",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("Windows liveness checks must not use os.kill")
        ),
    )

    assert notifications._pid_alive(4242) is True


def test_distinct_event_contention_reports_busy_not_duplicate(tmp_path, monkeypatch):
    from app.services import notifications

    _configured_env(monkeypatch)
    first_post_started = Event()
    release_post = Event()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(*args, **kwargs):
        first_post_started.set()
        release_post.wait(timeout=2)
        return Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    first_results = []
    first = Thread(
        target=lambda: first_results.append(
            notifications.send_alert(tmp_path, "first:event", "one")
        )
    )
    first.start()
    assert first_post_started.wait(timeout=1)

    second = notifications.send_alert(tmp_path, "second:event", "two")
    release_post.set()
    first.join(timeout=1)

    assert second == {"status": "busy"}
    assert first_results == [{"status": "sent"}]
