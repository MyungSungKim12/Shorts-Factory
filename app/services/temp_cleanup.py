"""Shorts Factory가 소유한 오래된 시스템 임시폴더만 정리한다."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


TEMP_PREFIX = "shorts-factory-"
OWNER_FILE = ".owner.json"


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def mark_temp_owner(path: Path) -> None:
    payload = {
        "pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (Path(path) / OWNER_FILE).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _owned_by_live_process(path: Path) -> bool:
    try:
        owner = json.loads((path / OWNER_FILE).read_text(encoding="utf-8"))
        return _process_alive(int(owner.get("pid", 0)))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def cleanup_stale_temp_dirs(
    now: datetime | None = None,
    max_age_seconds: int = 21600,
) -> dict:
    now = now or datetime.now(timezone.utc)
    temp_root = Path(tempfile.gettempdir()).resolve()
    removed_dirs = 0
    removed_bytes = 0
    for candidate in temp_root.iterdir():
        try:
            resolved = candidate.resolve()
            if (
                resolved.parent != temp_root
                or not resolved.name.startswith(TEMP_PREFIX)
                or not resolved.is_dir()
                or resolved.is_symlink()
            ):
                continue
            age = now.timestamp() - resolved.stat().st_mtime
            if age < max_age_seconds or _owned_by_live_process(resolved):
                continue
            size = _directory_bytes(resolved)
            shutil.rmtree(resolved)
            removed_dirs += 1
            removed_bytes += size
        except OSError:
            continue
    return {"removed_dirs": removed_dirs, "removed_bytes": removed_bytes}
