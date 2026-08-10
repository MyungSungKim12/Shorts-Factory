from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import manual_slot_pipeline
from app.services.manual_slot_pipeline import run_manual_prebuild
from app.services.slot_prebuild import (
    ensure_target_available,
    manual_reservation_for_prebuild,
)
from app.services.slot_reservations import (
    create_check,
    events_after,
    reserve_checked_topic,
    save_check_result,
)


KST = ZoneInfo("Asia/Seoul")
RUN_ID = "20260810-1"


def kst(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=KST)


def checked_topic() -> dict:
    return {
        "format": "story",
        "topic": "검증된 수동 소재",
        "category": "economy",
        "verification_method": "grounded_search",
        "verified_at": "2026-08-10T00:30:00+00:00",
    }


def reserve_manual_slot(data_dir: Path, run_id: str = RUN_ID) -> None:
    result = {
        "status": "reservable",
        "normalized_topic": "검증된 수동 소재",
        "topic_payload": checked_topic(),
        "visual": {"level": "high", "reservable": True},
    }
    create_check(data_dir, run_id, {"topic_input": "검증된 수동 소재"}, kst(8, 40))
    save_check_result(data_dir, run_id, result, kst(8, 41))
    reserve_checked_topic(data_dir, run_id, kst(8, 42))


def read_reservation(data_dir: Path, run_id: str = RUN_ID) -> dict:
    with sqlite3.connect(data_dir / "videos.sqlite") as db:
        db.row_factory = sqlite3.Row
        return dict(
            db.execute(
                "SELECT * FROM slot_reservations WHERE run_id = ?", (run_id,)
            ).fetchone()
        )


def _successful_boundaries(data_dir: Path, calls: list[str]):
    def fake_writer(root: Path, staging_id: str, **kwargs) -> dict:
        calls.append("writer")
        assert (root / "recovery" / "pipeline.lock").is_file()
        topic = json.loads(
            (root / "staging" / staging_id / "topic.json").read_text(
                encoding="utf-8"
            )
        )
        assert topic == checked_topic()
        script = {"title": topic["topic"], "scenes": [], "cta": "구독과 좋아요"}
        (root / "staging" / staging_id / "script.json").write_text(
            json.dumps(script, ensure_ascii=False), encoding="utf-8"
        )
        return script

    async def fake_producer(root: Path, staging_id: str, ffmpeg_path: str, **kwargs):
        calls.append("producer")
        assert (root / "recovery" / "pipeline.lock").is_file()
        staging = root / "staging" / staging_id
        script_bytes = (staging / "script.json").read_bytes()
        (staging / "output.mp4").write_bytes(b"video")
        (staging / "produce_log.json").write_text(
            json.dumps(
                {"script_sha256": hashlib.sha256(script_bytes).hexdigest()}
            ),
            encoding="utf-8",
        )
        return {"output": str(staging / "output.mp4")}

    return fake_writer, fake_producer


def test_manual_reservation_lookup_returns_only_checked_reserved_payload(tmp_path):
    assert manual_reservation_for_prebuild(tmp_path, RUN_ID) is None
    reserve_manual_slot(tmp_path)

    reservation = manual_reservation_for_prebuild(tmp_path, RUN_ID)

    assert reservation == {
        "run_id": RUN_ID,
        "state": "reserved",
        "attempt": 1,
        "topic_payload": checked_topic(),
        "production_at": "2026-08-10T09:00:00+09:00",
        "upload_at": "2026-08-10T11:00:00+09:00",
    }


def test_reserved_slot_uses_checked_topic_and_persists_review_artifact(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    calls: list[str] = []
    writer, producer = _successful_boundaries(tmp_path, calls)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))

    def fake_quality(staging: Path, ffmpeg_path: str) -> dict:
        calls.append("quality")
        assert (tmp_path / "recovery" / "pipeline.lock").is_file()
        return {"passed": True, "failures": [], "report": {"duration": 70.0}}

    monkeypatch.setattr(manual_slot_pipeline, "validate_upload_package", fake_quality)

    result = run_manual_prebuild(
        tmp_path,
        "ffmpeg",
        RUN_ID,
        writer_fn=writer,
        producer_fn=producer,
    )

    assert calls == ["writer", "producer", "quality"]
    assert result["state"] == "review_ready"
    assert result["destination"] == tmp_path / "work" / RUN_ID
    state = read_reservation(tmp_path)
    assert state["state"] == "review_ready"
    assert state["stage"] == "review_ready"
    assert state["worker_id"] is None
    assert state["artifact_path"].endswith(RUN_ID)
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()

    review = json.loads(
        (tmp_path / "work" / RUN_ID / "manual_review.json").read_text(
            encoding="utf-8"
        )
    )
    assert review == {
        "run_id": RUN_ID,
        "attempt": 1,
        "state": "review_ready",
        "topic_sha256": hashlib.sha256(
            (tmp_path / "work" / RUN_ID / "topic.json").read_bytes()
        ).hexdigest(),
    }
    assert [event["stage"] for event in events_after(tmp_path, RUN_ID, 0)] == [
        "researching",
        "writing",
        "producing",
        "quality_check",
        "review_ready",
    ]


def test_manual_failure_records_stage_releases_worker_and_hides_provider_error(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))

    def fake_writer(root: Path, staging_id: str, **kwargs):
        (root / "staging" / staging_id / "script.json").write_text(
            "{}", encoding="utf-8"
        )

    async def raising_producer(*args, **kwargs):
        raise RuntimeError("render failed token=provider-secret")

    with pytest.raises(RuntimeError, match="render failed"):
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=fake_writer,
            producer_fn=raising_producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "failed"
    assert state["stage"] == "producing"
    assert state["worker_id"] is None
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    events = events_after(tmp_path, RUN_ID, 0)
    assert events[-1]["level"] == "error"
    assert events[-1]["metadata"] == {"attempt": 1, "failed_stage": "producing"}
    assert "provider-secret" not in json.dumps(events, ensure_ascii=False)


def test_review_marker_is_staged_before_promotion(tmp_path, monkeypatch):
    reserve_manual_slot(tmp_path)
    calls: list[str] = []
    writer, producer = _successful_boundaries(tmp_path, calls)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    monkeypatch.setattr(
        manual_slot_pipeline,
        "validate_upload_package",
        lambda *args: {"passed": True, "failures": []},
    )
    real_promote = manual_slot_pipeline.promote_staging

    def assert_marker_then_promote(data_dir, staging_id, run_id, scheduled_at, quality):
        marker = data_dir / "staging" / staging_id / "manual_review.json"
        assert marker.is_file()
        return real_promote(data_dir, staging_id, run_id, scheduled_at, quality)

    monkeypatch.setattr(
        manual_slot_pipeline, "promote_staging", assert_marker_then_promote
    )

    result = run_manual_prebuild(
        tmp_path,
        "ffmpeg",
        RUN_ID,
        writer_fn=writer,
        producer_fn=producer,
    )

    assert result["state"] == "review_ready"


def test_review_marker_failure_never_creates_retry_blocking_work(tmp_path, monkeypatch):
    reserve_manual_slot(tmp_path)
    calls: list[str] = []
    writer, producer = _successful_boundaries(tmp_path, calls)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    monkeypatch.setattr(
        manual_slot_pipeline,
        "validate_upload_package",
        lambda *args: {"passed": True, "failures": []},
    )
    real_write_json = manual_slot_pipeline._write_json

    def fail_review_marker(path: Path, value: dict):
        if path.name == "manual_review.json":
            raise RuntimeError("marker write failed")
        return real_write_json(path, value)

    monkeypatch.setattr(manual_slot_pipeline, "_write_json", fail_review_marker)

    with pytest.raises(RuntimeError, match="marker write failed"):
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=writer,
            producer_fn=producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "failed"
    assert state["worker_id"] is None
    assert not (tmp_path / "work" / RUN_ID).exists()
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    ensure_target_available(tmp_path, RUN_ID)


def test_review_ready_transition_failure_archives_promoted_artifact(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    calls: list[str] = []
    writer, producer = _successful_boundaries(tmp_path, calls)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    monkeypatch.setattr(
        manual_slot_pipeline,
        "validate_upload_package",
        lambda *args: {"passed": True, "failures": []},
    )
    real_transition = manual_slot_pipeline.transition_slot

    def fail_review_ready(data_dir, run_id, expected, target, now, **fields):
        if target == "review_ready":
            raise RuntimeError("review state write failed")
        return real_transition(data_dir, run_id, expected, target, now, **fields)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", fail_review_ready)

    with pytest.raises(RuntimeError, match="review state write failed"):
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=writer,
            producer_fn=producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "failed"
    assert state["worker_id"] is None
    assert not (tmp_path / "work" / RUN_ID).exists()
    archives = list(
        (tmp_path / "recovery" / "manual-artifacts").glob(
            f"manual-prebuild-{RUN_ID}-1-*"
        )
    )
    assert len(archives) == 1
    assert (archives[0] / "output.mp4").is_file()
    assert (archives[0] / "manual_review.json").is_file()
    assert state["artifact_path"] == str(archives[0])
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    ensure_target_available(tmp_path, RUN_ID)


def test_failed_transition_uses_owned_fallback_before_lock_release(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    real_transition = manual_slot_pipeline.transition_slot

    def fail_normal_cleanup(data_dir, run_id, expected, target, now, **fields):
        if target == "failed":
            raise RuntimeError("transition unavailable")
        return real_transition(data_dir, run_id, expected, target, now, **fields)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", fail_normal_cleanup)

    def fake_writer(root: Path, staging_id: str, **kwargs):
        (root / "staging" / staging_id / "script.json").write_text(
            "{}", encoding="utf-8"
        )

    async def raising_producer(*args, **kwargs):
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=fake_writer,
            producer_fn=raising_producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "failed"
    assert state["worker_id"] is None
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()


def test_failure_event_error_is_reported_without_blocking_state_cleanup(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    real_append = manual_slot_pipeline.append_slot_event

    def fail_error_event(data_dir, run_id, stage, level, message, metadata=None):
        if level == "error":
            raise RuntimeError("event store included provider-secret")
        return real_append(data_dir, run_id, stage, level, message, metadata)

    monkeypatch.setattr(manual_slot_pipeline, "append_slot_event", fail_error_event)

    def fake_writer(root: Path, staging_id: str, **kwargs):
        (root / "staging" / staging_id / "script.json").write_text(
            "{}", encoding="utf-8"
        )

    async def raising_producer(*args, **kwargs):
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed") as captured:
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=fake_writer,
            producer_fn=raising_producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "failed"
    assert state["worker_id"] is None
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    notes = getattr(captured.value, "__notes__", [])
    assert notes == ["수동 제작 실패 이벤트 기록에 실패했습니다"]
    assert "provider-secret" not in json.dumps(notes, ensure_ascii=False)


def test_archive_failure_reports_paths_and_next_target_check_reconciles(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    calls: list[str] = []
    writer, producer = _successful_boundaries(tmp_path, calls)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    monkeypatch.setattr(
        manual_slot_pipeline,
        "validate_upload_package",
        lambda *args: {"passed": True, "failures": []},
    )
    real_transition = manual_slot_pipeline.transition_slot
    real_archive = manual_slot_pipeline._archive_promoted_artifact

    def fail_review_ready(data_dir, run_id, expected, target, now, **fields):
        if target == "review_ready":
            raise RuntimeError("review state write failed")
        return real_transition(data_dir, run_id, expected, target, now, **fields)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", fail_review_ready)
    monkeypatch.setattr(
        manual_slot_pipeline,
        "_archive_promoted_artifact",
        lambda *args: (_ for _ in ()).throw(RuntimeError("archive temporarily busy")),
    )

    with pytest.raises(RuntimeError, match="review state write failed"):
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=writer,
            producer_fn=producer,
        )

    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    current = tmp_path / "work" / RUN_ID
    assert current.is_dir()
    reports = list((tmp_path / "recovery" / "manual-cleanup").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["run_id"] == RUN_ID
    assert report["attempt"] == 1
    assert report["current_artifact_path"] == str(current)
    intended = Path(report["intended_recovery_path"])
    assert intended.parent == tmp_path / "recovery" / "manual-artifacts"
    assert "archive temporarily busy" not in json.dumps(report, ensure_ascii=False)

    ensure_target_available(tmp_path, RUN_ID)

    assert not current.exists()
    assert intended.is_dir()
    assert (intended / "output.mp4").is_file()
    assert not reports[0].exists()
    assert read_reservation(tmp_path)["artifact_path"] == str(intended)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", real_transition)
    monkeypatch.setattr(
        manual_slot_pipeline, "_archive_promoted_artifact", real_archive
    )
    create_check(
        tmp_path, RUN_ID, {"topic_input": "검증된 수동 소재"}, kst(8, 50)
    )
    save_check_result(
        tmp_path,
        RUN_ID,
        {
            "status": "reservable",
            "normalized_topic": "검증된 수동 소재",
            "topic_payload": checked_topic(),
            "visual": {"level": "high", "reservable": True},
        },
        kst(8, 51),
    )
    reserve_checked_topic(tmp_path, RUN_ID, kst(8, 52))

    retried = run_manual_prebuild(
        tmp_path,
        "ffmpeg",
        RUN_ID,
        writer_fn=writer,
        producer_fn=producer,
    )

    assert retried["state"] == "review_ready"
    assert retried["attempt"] == 2
    assert retried["destination"] == tmp_path / "work" / RUN_ID


def test_total_state_cleanup_failure_releases_lock_and_next_lookup_reconciles(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    real_transition = manual_slot_pipeline.transition_slot

    def fail_normal_cleanup(data_dir, run_id, expected, target, now, **fields):
        if target == "failed":
            raise RuntimeError("transition unavailable")
        return real_transition(data_dir, run_id, expected, target, now, **fields)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", fail_normal_cleanup)
    monkeypatch.setattr(
        manual_slot_pipeline,
        "fail_owned_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("fallback unavailable")
        ),
    )

    def fake_writer(root: Path, staging_id: str, **kwargs):
        (root / "staging" / staging_id / "script.json").write_text(
            "{}", encoding="utf-8"
        )

    async def raising_producer(*args, **kwargs):
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed") as captured:
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=fake_writer,
            producer_fn=raising_producer,
        )

    state = read_reservation(tmp_path)
    assert state["state"] == "producing"
    assert state["worker_id"] is not None
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
    report_path = tmp_path / "recovery" / "manual-cleanup" / f"{RUN_ID}-1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["worker_id"] == state["worker_id"]
    assert report["failed_stage"] == "producing"
    assert getattr(captured.value, "__notes__", []) == [
        "수동 제작 실패 상태 정리에 실패했습니다"
    ]
    assert "fallback unavailable" not in json.dumps(report, ensure_ascii=False)

    reservation = manual_reservation_for_prebuild(tmp_path, RUN_ID)

    reconciled = read_reservation(tmp_path)
    assert reconciled["state"] == "failed"
    assert reconciled["worker_id"] is None
    assert reservation["state"] == "failed"
    assert not report_path.exists()


def test_cleanup_report_failure_is_safe_and_does_not_mask_original_error(
    tmp_path, monkeypatch
):
    reserve_manual_slot(tmp_path)
    monkeypatch.setattr(manual_slot_pipeline, "_now", lambda: kst(9))
    real_transition = manual_slot_pipeline.transition_slot

    def fail_normal_cleanup(data_dir, run_id, expected, target, now, **fields):
        if target == "failed":
            raise RuntimeError("transition unavailable")
        return real_transition(data_dir, run_id, expected, target, now, **fields)

    monkeypatch.setattr(manual_slot_pipeline, "transition_slot", fail_normal_cleanup)
    monkeypatch.setattr(
        manual_slot_pipeline,
        "fail_owned_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("fallback unavailable")
        ),
    )
    monkeypatch.setattr(
        manual_slot_pipeline,
        "_write_cleanup_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("report failed provider-secret")
        ),
    )

    def fake_writer(root: Path, staging_id: str, **kwargs):
        (root / "staging" / staging_id / "script.json").write_text(
            "{}", encoding="utf-8"
        )

    async def raising_producer(*args, **kwargs):
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed") as captured:
        run_manual_prebuild(
            tmp_path,
            "ffmpeg",
            RUN_ID,
            writer_fn=fake_writer,
            producer_fn=raising_producer,
        )

    notes = getattr(captured.value, "__notes__", [])
    assert notes == [
        "수동 제작 실패 상태 정리에 실패했습니다",
        "수동 제작 정리 보고서 기록에 실패했습니다",
    ]
    assert "provider-secret" not in json.dumps(notes, ensure_ascii=False)
    assert not (tmp_path / "recovery" / "pipeline.lock").exists()
