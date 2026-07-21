import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.services import recovery
from scripts import run_scheduled as command


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


def test_global_lock_waits_for_living_other_slot_then_reclaims(tmp_path, monkeypatch):
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    lock = recovery_dir / "pipeline.lock"
    lock.write_text(json.dumps({
        "pid": 777, "run_id": "20260721-1", "started_at": FIXED_NOW.isoformat(),
    }), encoding="utf-8")
    alive_checks = iter([True, False])
    monkeypatch.setattr(recovery, "_process_alive", lambda pid: next(alive_checks))
    sleeps = []

    async def no_wait(seconds):
        sleeps.append(seconds)

    async def pipeline(data_dir, ffmpeg_path, slot):
        return {"date": "20260721-2", "success": True}

    result = asyncio.run(recovery.run_with_recovery(
        tmp_path, "ffmpeg", 2, pipeline_runner=pipeline,
        sleep_fn=no_wait, now_fn=lambda: FIXED_NOW,
        lock_wait_seconds=2, lock_poll_seconds=1,
    ))

    assert result["success"] is True
    assert sleeps == [1]
    assert not lock.exists()


def test_unreadable_global_lock_times_out_without_running_pipeline(tmp_path):
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    (recovery_dir / "pipeline.lock").write_text("broken", encoding="utf-8")
    calls = []

    async def pipeline(*args, **kwargs):
        calls.append(True)
        return {"success": True}

    with pytest.raises(RuntimeError, match="전역 파이프라인 잠금 대기 시간 초과"):
        asyncio.run(recovery.run_with_recovery(
            tmp_path, "ffmpeg", 2, pipeline_runner=pipeline,
            sleep_fn=lambda _: asyncio.sleep(0), now_fn=lambda: FIXED_NOW,
            lock_wait_seconds=0, lock_poll_seconds=1,
        ))

    assert calls == []
    assert (recovery_dir / "pipeline.lock").read_text() == "broken"


def test_release_owned_global_lock_preserves_changed_owner(tmp_path):
    lock = tmp_path / "pipeline.lock"
    lock.write_text(json.dumps({
        "pid": 999, "run_id": "other", "started_at": FIXED_NOW.isoformat(),
    }), encoding="utf-8")

    recovery.release_owned_lock(lock, "mine", 123)

    assert lock.exists()


def test_scheduled_runner_alerts_uploaded_url(tmp_path, monkeypatch):
    alerts = []

    async def uploaded(*args, **kwargs):
        return {
            "date": "20260721-1",
            "success": True,
            "stages": {"uploader": {"status": "uploaded", "url": "https://youtu.be/abc"}},
        }

    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(command, "cleanup_stale_temp_dirs", lambda: {"removed_dirs": 0, "removed_bytes": 0})
    monkeypatch.setattr(command, "cleanup_old_work", lambda *args: None)
    monkeypatch.setattr(command, "run_with_recovery", uploaded)
    monkeypatch.setattr(
        command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)), raising=False
    )
    monkeypatch.setattr(command.sys, "argv", ["run_scheduled.py", "1"])

    command.main()

    assert alerts == [
        (
            (tmp_path, "upload:20260721-1:uploaded"),
            {"text": "Scheduled upload succeeded\nrun_id: 20260721-1\nurl: https://youtu.be/abc"},
        )
    ]


def test_scheduled_runner_alerts_recovery_exhaustion_before_exit(tmp_path, monkeypatch):
    alerts = []
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    (recovery_dir / "20260721-1.json").write_text(
        json.dumps({
            "failed_stage": "writer",
            "last_error": f"POST https://api.telegram.org/bot{token}/sendMessage failed",
        }),
        encoding="utf-8",
    )

    async def exhausted(*args, **kwargs):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(command, "cleanup_stale_temp_dirs", lambda: {"removed_dirs": 0, "removed_bytes": 0})
    monkeypatch.setattr(command, "cleanup_old_work", lambda *args: None)
    monkeypatch.setattr(command, "run_with_recovery", exhausted)
    monkeypatch.setattr(
        command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)), raising=False
    )
    monkeypatch.setattr(command.sys, "argv", ["run_scheduled.py", "1"])
    monkeypatch.setattr(command, "_scheduled_run_id", lambda slot: "20260721-1", raising=False)

    with pytest.raises(SystemExit, match="1"):
        command.main()

    assert alerts == [
        (
            (tmp_path, "recovery:20260721-1:exhausted"),
            {"text": "Recovery exhausted\nrun_id: 20260721-1\nstage: writer\nerror_category: pipeline_failure"},
        )
    ]
    assert token not in alerts[0][1]["text"]


def test_scheduled_runner_maps_unknown_skip_reason_to_safe_category(tmp_path, monkeypatch):
    alerts = []
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"

    async def skipped(*args, **kwargs):
        return {
            "date": f"https://api.telegram.org/bot{token}/sendMessage",
            "success": True,
            "stages": {
                "uploader": {
                    "status": "skipped",
                    "reason": f"POST https://api.telegram.org/bot{token}/sendMessage failed",
                }
            },
        }

    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(command, "cleanup_stale_temp_dirs", lambda: {"removed_dirs": 0, "removed_bytes": 0})
    monkeypatch.setattr(command, "cleanup_old_work", lambda *args: None)
    monkeypatch.setattr(command, "run_with_recovery", skipped)
    monkeypatch.setattr(command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)))
    monkeypatch.setattr(command.sys, "argv", ["run_scheduled.py", "1"])

    command.main()

    assert alerts[0][1]["text"].endswith("reason: unknown")
    assert "run_id: unknown" in alerts[0][1]["text"]
    assert token not in alerts[0][1]["text"]


def test_scheduled_runner_preserves_known_skip_reason_category(tmp_path, monkeypatch):
    alerts = []

    async def skipped(*args, **kwargs):
        return {
            "date": "20260721-1",
            "success": True,
            "stages": {"uploader": {"status": "skipped", "reason": "오늘 영상 이미 업로드됨"}},
        }

    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(command, "cleanup_stale_temp_dirs", lambda: {"removed_dirs": 0, "removed_bytes": 0})
    monkeypatch.setattr(command, "cleanup_old_work", lambda *args: None)
    monkeypatch.setattr(command, "run_with_recovery", skipped)
    monkeypatch.setattr(command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)))
    monkeypatch.setattr(command.sys, "argv", ["run_scheduled.py", "1"])

    command.main()

    assert alerts[0][1]["text"].endswith("reason: already_uploaded")
