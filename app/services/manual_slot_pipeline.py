"""Checked manual-topic prebuilds that stop at persistent human review."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.agents.producer import run_producer
from app.agents.writer import run_writer
from app.services.quality_gate import validate_upload_package
from app.services.recovery import acquire_global_lock, release_owned_lock
from app.services.slot_prebuild import manual_reservation_for_prebuild, promote_staging
from app.services.slot_reservations import (
    ACTIVE_STATES,
    KST,
    append_slot_event,
    lock_reserved_slot,
    slot_window,
    transition_slot,
)


STAGES = (
    ("researching", "검증된 소재를 준비했습니다"),
    ("writing", "대본을 작성하고 있습니다"),
    ("producing", "음성·영상·자막을 합성하고 있습니다"),
    ("quality_check", "최종 영상 품질을 검사하고 있습니다"),
)


def _now() -> datetime:
    return datetime.now(tz=KST)


def _write_json(path: Path, value: dict) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(encoded)
    return encoded


def _run_producer_boundary(producer_fn: Callable, *args, **kwargs):
    result = producer_fn(*args, **kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


def _checked_topic(locked: dict) -> dict:
    checked = locked.get("check_result")
    topic = checked.get("topic_payload") if isinstance(checked, dict) else None
    if not isinstance(topic, dict):
        raise RuntimeError("수동 예약의 검증 소재가 유효하지 않습니다")
    return topic


def _tag_boundary(exc: Exception, boundary: str) -> None:
    if not hasattr(exc, "prebuild_stage"):
        try:
            setattr(exc, "prebuild_stage", boundary)
        except Exception:
            pass


def run_manual_prebuild(
    data_dir: Path,
    ffmpeg_path: str,
    run_id: str,
    *,
    writer_fn=run_writer,
    producer_fn=run_producer,
) -> dict:
    """Build one reserved manual slot under the global lock and await review."""
    data_dir = Path(data_dir)
    reservation = manual_reservation_for_prebuild(data_dir, run_id)
    if reservation is None:
        raise RuntimeError("사전 제작할 수동 예약이 없습니다")
    attempt = int(reservation["attempt"])
    worker_id = f"manual-prebuild:{run_id}:{attempt}:{os.getpid()}"
    staging_id = f"manual-prebuild-{run_id}-{attempt}"
    staging_dir = data_dir / "staging" / staging_id
    lock_path = data_dir / "recovery" / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not acquire_global_lock(lock_path, worker_id, _now()):
        raise RuntimeError("수동 사전 제작 전역 파이프라인 잠금 획득 실패")

    current_state: str | None = None
    boundary = "lock"
    previous_pipeline_run_id = os.environ.get("PIPELINE_RUN_ID")
    os.environ["PIPELINE_RUN_ID"] = run_id
    try:
        locked = lock_reserved_slot(data_dir, run_id, worker_id, _now())
        if locked is None:
            raise RuntimeError("수동 예약을 제작 작업자가 잠그지 못했습니다")
        current_state = "locked"
        topic = _checked_topic(locked)
        selected = topic.get("format")
        if not isinstance(selected, str) or not selected:
            raise RuntimeError("수동 예약 소재 형식이 없습니다")
        staging_dir.mkdir(parents=True, exist_ok=False)

        boundary = "researcher"
        transition_slot(
            data_dir,
            run_id,
            {current_state},
            "researching",
            _now(),
            stage="researching",
        )
        current_state = "researching"
        append_slot_event(
            data_dir,
            run_id,
            current_state,
            "info",
            STAGES[0][1],
            {"attempt": attempt},
        )
        topic_bytes = _write_json(staging_dir / "topic.json", topic)
        topic_sha256 = hashlib.sha256(topic_bytes).hexdigest()

        boundary = "writer"
        transition_slot(
            data_dir,
            run_id,
            {current_state},
            "writing",
            _now(),
            stage="writing",
        )
        current_state = "writing"
        append_slot_event(
            data_dir,
            run_id,
            current_state,
            "info",
            STAGES[1][1],
            {"attempt": attempt},
        )
        writer_fn(
            data_dir,
            staging_id,
            content_format=selected,
            work_root="staging",
            manual_checked=True,
        )

        boundary = "producer"
        transition_slot(
            data_dir,
            run_id,
            {current_state},
            "producing",
            _now(),
            stage="producing",
        )
        current_state = "producing"
        append_slot_event(
            data_dir,
            run_id,
            current_state,
            "info",
            STAGES[2][1],
            {"attempt": attempt},
        )
        _run_producer_boundary(
            producer_fn,
            data_dir,
            staging_id,
            ffmpeg_path,
            content_format=selected,
            work_root="staging",
        )

        boundary = "quality_gate"
        transition_slot(
            data_dir,
            run_id,
            {current_state},
            "quality_check",
            _now(),
            stage="quality_check",
        )
        current_state = "quality_check"
        append_slot_event(
            data_dir,
            run_id,
            current_state,
            "info",
            STAGES[3][1],
            {"attempt": attempt},
        )
        quality = validate_upload_package(staging_dir, ffmpeg_path)

        boundary = "promotion"
        scheduled_at = slot_window(run_id).upload_at
        destination = promote_staging(
            data_dir, staging_id, run_id, scheduled_at, quality
        )
        _write_json(
            destination / "manual_review.json",
            {
                "run_id": run_id,
                "attempt": attempt,
                "state": "review_ready",
                "topic_sha256": topic_sha256,
            },
        )
        transition_slot(
            data_dir,
            run_id,
            {current_state},
            "review_ready",
            _now(),
            stage="review_ready",
            worker_id=None,
            artifact_path=str(destination),
        )
        current_state = "review_ready"
        append_slot_event(
            data_dir,
            run_id,
            current_state,
            "info",
            "검토할 영상이 준비되었습니다",
            {"attempt": attempt},
        )
        return {
            "run_id": run_id,
            "scheduled_at": scheduled_at,
            "destination": destination,
            "quality_gate": quality,
            "state": current_state,
            "attempt": attempt,
        }
    except Exception as exc:
        _tag_boundary(exc, boundary)
        if current_state in ACTIVE_STATES:
            failed_stage = current_state
            try:
                transition_slot(
                    data_dir,
                    run_id,
                    {current_state},
                    "failed",
                    _now(),
                    stage=failed_stage,
                    worker_id=None,
                )
                append_slot_event(
                    data_dir,
                    run_id,
                    failed_stage,
                    "error",
                    "수동 영상 제작 중 오류가 발생했습니다",
                    {"attempt": attempt, "failed_stage": failed_stage},
                )
            except Exception:
                pass
        raise
    finally:
        if previous_pipeline_run_id is None:
            os.environ.pop("PIPELINE_RUN_ID", None)
        else:
            os.environ["PIPELINE_RUN_ID"] = previous_pipeline_run_id
        release_owned_lock(lock_path, worker_id, os.getpid())
