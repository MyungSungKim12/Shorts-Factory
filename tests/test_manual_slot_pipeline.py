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
from app.services.slot_prebuild import manual_reservation_for_prebuild
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
