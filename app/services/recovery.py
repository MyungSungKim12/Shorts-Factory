"""예약 파이프라인의 중복 실행 방지와 안전한 1회 복구."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable


RETRYABLE_STAGES = {"researcher", "writer", "producer"}


def retry_at(failed_at: datetime, delay_seconds: int = 900) -> datetime:
    return failed_at + timedelta(seconds=delay_seconds)


def load_uploaded_dates(data_dir: Path) -> set[str]:
    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return set()
    try:
        with sqlite3.connect(db_file) as db:
            rows = db.execute(
                "SELECT date FROM videos WHERE status = 'uploaded'"
            ).fetchall()
        return {str(row[0]) for row in rows}
    except (sqlite3.Error, OSError):
        # 업로드 DB를 확실히 읽을 수 없으면 재업로드하지 않는 쪽이 안전하다.
        return {"__database_unavailable__"}


def failed_stage(run_log: dict) -> str:
    stages = run_log.get("stages", {})
    for name in ("researcher", "writer", "producer", "uploader"):
        if stages.get(name, {}).get("status") == "error":
            return name
    return "unknown"


def is_safe_to_retry(run_log: dict, uploaded_dates: set[str]) -> bool:
    run_id = str(run_log.get("date", ""))
    if "__database_unavailable__" in uploaded_dates or run_id in uploaded_dates:
        return False

    uploader = run_log.get("stages", {}).get("uploader")
    if uploader is not None:
        # 업로더까지 진입한 실행은 API 응답 유실 가능성이 있어 자동 재업로드하지 않는다.
        return False
    return failed_stage(run_log) in RETRYABLE_STAGES


def _read_run_log(data_dir: Path, run_id: str) -> dict:
    path = data_dir / "logs" / f"run-{run_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _lock_process_alive(lock_path: Path) -> bool:
    try:
        pid = int(lock_path.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def reconcile_stale_states(data_dir: Path, current_run_id: str, now: datetime) -> None:
    """다음 회차 시작 시 종료된 이전 회차의 중간 상태를 확정한다."""
    recovery_dir = data_dir / "recovery"
    if not recovery_dir.exists():
        return
    current_date, _, current_slot_text = current_run_id.partition("-")
    current_slot = int(current_slot_text)
    for path in recovery_dir.glob(f"{current_date}-*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            _, _, slot_text = str(state.get("run_id", "")).partition("-")
            if int(slot_text) >= current_slot:
                continue
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if state.get("status") not in {"scheduled", "running"}:
            continue
        lock_path = path.with_suffix(".lock")
        if lock_path.exists() and _lock_process_alive(lock_path):
            continue
        run_log = _read_run_log(data_dir, str(state.get("run_id", "")))
        state["status"] = "recovered" if run_log.get("success") is True else "exhausted"
        state["updated_at"] = now.isoformat()
        _write_state(path, state)
        lock_path.unlink(missing_ok=True)


def _state(
    run_id: str,
    attempts: int,
    status: str,
    now: datetime,
    *,
    stage: str = "",
    error: str = "",
    next_retry: datetime | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "attempts": attempts,
        "status": status,
        "failed_stage": stage,
        "last_error": error,
        "next_retry_at": next_retry.isoformat() if next_retry else None,
        "updated_at": now.isoformat(),
    }


async def run_with_recovery(
    data_dir: Path,
    ffmpeg_path: str,
    slot: int,
    delay_seconds: int = 900,
    *,
    pipeline_runner: Callable[..., Awaitable[dict]] | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict:
    """동일 회차를 잠그고, 업로드 전 명확한 실패만 한 번 재시도한다."""
    if pipeline_runner is None:
        from app.agents.orchestrator import run_pipeline
        pipeline_runner = run_pipeline
    sleep_fn = sleep_fn or asyncio.sleep
    now_fn = now_fn or (lambda: datetime.now().astimezone())

    started_at = now_fn()
    run_id = f"{started_at.strftime('%Y%m%d')}-{slot}"
    recovery_dir = data_dir / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    reconcile_stale_states(data_dir, run_id, started_at)
    lock_path = recovery_dir / f"{run_id}.lock"
    state_path = recovery_dir / f"{run_id}.json"

    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"status": "already_running", "run_id": run_id}

    os.write(descriptor, str(os.getpid()).encode("ascii"))
    os.close(descriptor)
    scheduled_retry = None
    first_stage = ""
    first_error = ""
    try:
        for attempt in (1, 2):
            now = now_fn()
            _write_state(
                state_path,
                _state(
                    run_id, attempt, "running", now,
                    stage=first_stage, error=first_error, next_retry=scheduled_retry,
                ),
            )
            try:
                result = pipeline_runner(data_dir, ffmpeg_path, slot=slot)
                if inspect.isawaitable(result):
                    result = await result
                if attempt == 1:
                    state_path.unlink(missing_ok=True)
                else:
                    _write_state(
                        state_path,
                        _state(
                            run_id, 2, "recovered", now_fn(),
                            stage=first_stage, error=first_error,
                            next_retry=scheduled_retry,
                        ),
                    )
                return result
            except Exception as exc:
                run_log = _read_run_log(data_dir, run_id)
                stage = failed_stage(run_log)
                if attempt == 1 and is_safe_to_retry(
                    run_log, load_uploaded_dates(data_dir)
                ):
                    first_stage = stage
                    first_error = str(exc)
                    scheduled_retry = retry_at(now_fn(), delay_seconds)
                    _write_state(
                        state_path,
                        _state(
                            run_id, 1, "scheduled", now_fn(),
                            stage=stage, error=str(exc), next_retry=scheduled_retry,
                        ),
                    )
                    await sleep_fn(delay_seconds)
                    continue

                _write_state(
                    state_path,
                    _state(
                        run_id, attempt, "exhausted", now_fn(),
                        stage=stage or first_stage, error=str(exc),
                        next_retry=scheduled_retry,
                    ),
                )
                raise
        raise RuntimeError("복구 실행 횟수 제한을 초과했습니다")
    finally:
        lock_path.unlink(missing_ok=True)
