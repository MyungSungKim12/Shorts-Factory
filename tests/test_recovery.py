import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.services import recovery


FIXED_NOW = datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)


def _failed_log(stage="writer", uploader=None):
    stages = {stage: {"status": "error", "error": f"{stage} failed"}}
    if uploader is not None:
        stages["uploader"] = uploader
    return {"date": "20260721-1", "success": False, "stages": stages}


def test_only_clear_pre_upload_failures_are_retryable():
    assert recovery.is_safe_to_retry(_failed_log("writer"), set())
    assert recovery.is_safe_to_retry(_failed_log("producer"), set())
    assert not recovery.is_safe_to_retry(
        _failed_log("writer", {"status": "uploaded", "video_id": "abc"}), set()
    )
    assert not recovery.is_safe_to_retry(_failed_log("uploader"), set())
    assert not recovery.is_safe_to_retry(_failed_log("writer"), {"20260721-1"})


def test_retry_time_is_fifteen_minutes_after_failure():
    assert recovery.retry_at(FIXED_NOW, 900).isoformat() == "2026-07-21T11:15:00+00:00"


def test_uploaded_dates_are_loaded_from_sqlite(tmp_path):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute("CREATE TABLE videos (video_id TEXT, date TEXT, status TEXT)")
    db.execute("INSERT INTO videos VALUES ('abc', '20260721-1', 'uploaded')")
    db.commit()
    db.close()
    assert recovery.load_uploaded_dates(tmp_path) == {"20260721-1"}


def test_safe_failure_retries_once_and_records_recovered(tmp_path):
    calls = []
    sleeps = []

    async def pipeline(data_dir, ffmpeg_path, slot):
        calls.append(slot)
        if len(calls) == 1:
            logs = data_dir / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "run-20260721-1.json").write_text(
                json.dumps(_failed_log("producer")), encoding="utf-8"
            )
            raise RuntimeError("producer failed")
        return {"date": "20260721-1", "success": True}

    async def no_wait(seconds):
        sleeps.append(seconds)

    result = asyncio.run(recovery.run_with_recovery(
        tmp_path, "ffmpeg", 1, delay_seconds=900,
        pipeline_runner=pipeline, sleep_fn=no_wait, now_fn=lambda: FIXED_NOW,
    ))

    assert result["success"] is True
    assert calls == [1, 1]
    assert sleeps == [900]
    state = json.loads((tmp_path / "recovery" / "20260721-1.json").read_text())
    assert state["status"] == "recovered"
    assert state["attempts"] == 2
    assert state["failed_stage"] == "producer"
    assert state["next_retry_at"] == "2026-07-21T11:15:00+00:00"


def test_second_failure_is_exhausted_and_never_calls_third_time(tmp_path):
    calls = []

    async def pipeline(data_dir, ffmpeg_path, slot):
        calls.append(slot)
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "run-20260721-1.json").write_text(
            json.dumps(_failed_log("writer")), encoding="utf-8"
        )
        raise RuntimeError("writer failed")

    with pytest.raises(RuntimeError, match="writer failed"):
        asyncio.run(recovery.run_with_recovery(
            tmp_path, "ffmpeg", 1, delay_seconds=0,
            pipeline_runner=pipeline, sleep_fn=lambda _: asyncio.sleep(0),
            now_fn=lambda: FIXED_NOW,
        ))

    assert calls == [1, 1]
    state = json.loads((tmp_path / "recovery" / "20260721-1.json").read_text())
    assert state["status"] == "exhausted"
    assert state["attempts"] == 2


def test_ambiguous_uploader_failure_does_not_retry(tmp_path):
    calls = []

    async def pipeline(data_dir, ffmpeg_path, slot):
        calls.append(slot)
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = _failed_log("uploader")
        (logs / "run-20260721-1.json").write_text(json.dumps(log), encoding="utf-8")
        raise RuntimeError("upload response lost")

    with pytest.raises(RuntimeError, match="upload response lost"):
        asyncio.run(recovery.run_with_recovery(
            tmp_path, "ffmpeg", 1, delay_seconds=0,
            pipeline_runner=pipeline, now_fn=lambda: FIXED_NOW,
        ))

    assert calls == [1]
    state = json.loads((tmp_path / "recovery" / "20260721-1.json").read_text())
    assert state["status"] == "exhausted"


def test_concurrent_slot_returns_already_running(tmp_path):
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    (recovery_dir / "20260721-1.lock").write_text("existing", encoding="utf-8")

    result = asyncio.run(recovery.run_with_recovery(
        tmp_path, "ffmpeg", 1, now_fn=lambda: FIXED_NOW,
    ))
    assert result == {"status": "already_running", "run_id": "20260721-1"}


def test_next_slot_normalizes_stale_previous_state(tmp_path):
    recovery_dir = tmp_path / "recovery"
    logs_dir = tmp_path / "logs"
    recovery_dir.mkdir()
    logs_dir.mkdir()
    stale = {
        "run_id": "20260721-1", "attempts": 2, "status": "running",
        "failed_stage": "producer", "last_error": "lost process",
        "next_retry_at": None, "updated_at": FIXED_NOW.isoformat(),
    }
    (recovery_dir / "20260721-1.json").write_text(json.dumps(stale), encoding="utf-8")
    (logs_dir / "run-20260721-1.json").write_text(
        json.dumps({"date": "20260721-1", "success": False}), encoding="utf-8"
    )

    recovery.reconcile_stale_states(tmp_path, "20260721-2", FIXED_NOW)

    normalized = json.loads((recovery_dir / "20260721-1.json").read_text())
    assert normalized["status"] == "exhausted"
