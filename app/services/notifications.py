"""Best-effort Telegram alerts that cannot affect pipeline execution."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


_DEDUPLICATION_TTL = timedelta(hours=24)
_LOCK_TTL = timedelta(minutes=5)
_LOCK_RETRIES = 3
_LOCK_RETRY_DELAY_SECONDS = 0.01
_BOT_TOKEN_PATTERN = re.compile(r"(?<!\d)\d{5,15}:[A-Za-z0-9_-]{20,}")


def safe_error(exc: Exception) -> str:
    """Return a bounded diagnostic that cannot disclose a Bot API token."""
    message = str(exc) or exc.__class__.__name__
    return _BOT_TOKEN_PATTERN.sub("[redacted]", message)[:300]


def _state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "notifications" / "state.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _windows_pid_alive(pid: int) -> bool:
    """Probe a Windows process without sending it a terminating signal."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5  # access denied still means it exists
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


def _claim_state_lock(
    path: Path, event_key: str, now: datetime
) -> tuple[str, str | None]:
    """Return acquired/duplicate/busy while recovering only dead or expired owners."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "pid": os.getpid(),
        "event_key": event_key,
        "started_at": now.isoformat(),
    }
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            started_at = _as_utc(datetime.fromisoformat(owner["started_at"]))
            owner_pid = int(owner["pid"])
            owner_event = owner["event_key"]
            if not isinstance(owner_event, str):
                return "busy", None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return "busy", None
        if now - started_at >= _LOCK_TTL or not _pid_alive(owner_pid):
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return "busy", None
            return _claim_state_lock(path, event_key, now)
        if owner_event == event_key:
            return "duplicate", None
        return "busy", None
    try:
        os.write(
            descriptor,
            json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8"),
        )
    finally:
        os.close(descriptor)
    return "acquired", token


def _acquire_state_lock(
    path: Path, event_key: str = "", now: datetime | None = None
) -> str | None:
    """Compatibility wrapper returning only a newly acquired owner token."""
    status, token = _claim_state_lock(
        path, event_key, _as_utc(now or datetime.now(timezone.utc))
    )
    return token if status == "acquired" else None


def _release_state_lock(path: Path, token: str) -> None:
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        if payload.get("token") == token:
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
    enabled = os.getenv("TELEGRAM_ALERTS_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return {"status": "disabled"}
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"status": "disabled"}

    current_time = _as_utc(now or datetime.now(timezone.utc))
    path = _state_path(Path(data_dir))
    try:
        lock_status = "busy"
        lock_token = None
        for attempt in range(_LOCK_RETRIES):
            lock_status, lock_token = _claim_state_lock(
                path, event_key, current_time
            )
            if lock_status != "busy":
                break
            if attempt + 1 < _LOCK_RETRIES:
                time.sleep(_LOCK_RETRY_DELAY_SECONDS)
    except Exception as exc:
        return {"status": "error", "error": safe_error(exc)}
    if lock_status == "duplicate":
        return {"status": "duplicate"}
    if lock_status == "busy" or lock_token is None:
        return {"status": "busy"}
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
