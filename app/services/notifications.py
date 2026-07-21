"""Best-effort Telegram alerts that cannot affect pipeline execution."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


_DEDUPLICATION_TTL = timedelta(hours=24)
_BOT_TOKEN_PATTERN = re.compile(r"(?<!\d)\d{5,15}:[A-Za-z0-9_-]{20,}")


def safe_error(exc: Exception) -> str:
    """Return a bounded diagnostic that cannot disclose a Bot API token."""
    message = str(exc) or exc.__class__.__name__
    return _BOT_TOKEN_PATTERN.sub("[redacted]", message)[:300]


def _state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "notifications" / "state.json"


def _acquire_state_lock(path: Path) -> str | None:
    """Claim the state file so simultaneous schedulers cannot both post an event."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    token = uuid.uuid4().hex
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(descriptor, token.encode("ascii"))
    finally:
        os.close(descriptor)
    return token


def _release_state_lock(path: Path, token: str) -> None:
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        if lock_path.read_text(encoding="ascii") == token:
            lock_path.unlink()
    except Exception:
        return


def _read_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("notification state must be an object")
    events = payload.get("events", payload)
    if not isinstance(events, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in events.items()
    ):
        raise ValueError("notification state contains invalid events")
    return dict(events)


def _write_state(path: Path, events: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump({"events": events}, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_duplicate(events: dict[str, str], event_key: str, now: datetime) -> bool:
    timestamp = events.get(event_key)
    if timestamp is None:
        return False
    try:
        sent_at = _as_utc(datetime.fromisoformat(timestamp))
    except ValueError as exc:
        raise ValueError("notification state contains invalid timestamp") from exc
    return now - sent_at < _DEDUPLICATION_TTL


def _retained_events(events: dict[str, str], now: datetime) -> dict[str, str]:
    retained: dict[str, str] = {}
    for key, timestamp in events.items():
        try:
            sent_at = _as_utc(datetime.fromisoformat(timestamp))
        except ValueError as exc:
            raise ValueError("notification state contains invalid timestamp") from exc
        if now - sent_at < _DEDUPLICATION_TTL:
            retained[key] = timestamp
    return retained


def send_alert(
    data_dir: Path,
    event_key: str,
    text: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Send one deduplicated alert, returning status instead of ever raising."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"status": "disabled"}

    current_time = _as_utc(now or datetime.now(timezone.utc))
    path = _state_path(Path(data_dir))
    try:
        lock_token = _acquire_state_lock(path)
    except Exception as exc:
        return {"status": "error", "error": safe_error(exc)}
    if lock_token is None:
        return {"status": "duplicate"}
    try:
        try:
            events = _read_state(path)
            if _is_duplicate(events, event_key, current_time):
                return {"status": "duplicate"}
        except Exception as exc:
            return {"status": "error", "error": safe_error(exc)}

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text},
                timeout=(5, 10),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                return {"status": "error", "error": "Telegram rejected the alert"}
        except Exception as exc:
            return {"status": "error", "error": safe_error(exc)}

        try:
            retained = _retained_events(events, current_time)
            retained[event_key] = current_time.isoformat()
            _write_state(path, retained)
        except Exception as exc:
            return {"status": "error", "error": safe_error(exc)}
        return {"status": "sent"}
    finally:
        _release_state_lock(path, lock_token)
