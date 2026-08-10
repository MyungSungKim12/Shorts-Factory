"""Approval and artifact lifecycle actions for manually reserved slots."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.services.slot_reservations import (
    KST,
    SlotConflict,
    append_slot_event,
    init_slot_tables,
    slot_window,
)
from app.services.temp_cleanup import (
    _process_alive,
    cleanup_rejected_artifacts,
)


UploadDecision = Literal["automatic", "approved", "hold"]
RetryMode = Literal["same_topic", "new_topic"]
_JSON_COLUMNS = {
    "include_constraints",
    "exclude_constraints",
    "reference_links",
    "request_json",
    "check_result",
}


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _connect(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(data_dir) / "videos.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _row_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    for name in _JSON_COLUMNS:
        value = result.get(name)
        result[name] = json.loads(value) if value else None
    return result


def _fetch(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM slot_reservations WHERE run_id = ? AND mode = 'manual'",
        (run_id,),
    ).fetchone()


def _begin(data_dir: Path) -> sqlite3.Connection:
    init_slot_tables(data_dir)
    db = _connect(data_dir)
    db.execute("BEGIN IMMEDIATE")
    return db


def _timestamp(now: datetime) -> str:
    return _as_kst(now).isoformat()


def _assert_no_live_global_lock(data_dir: Path) -> None:
    lock = Path(data_dir) / "recovery" / "pipeline.lock"
    if not lock.exists():
        return
    try:
        snapshot = lock.read_text(encoding="utf-8")
        owner = json.loads(snapshot)
        pid = int(owner["pid"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SlotConflict("전역 파이프라인 잠금 해제를 확인할 수 없습니다") from exc
    if _process_alive(pid):
        raise SlotConflict("전역 파이프라인 잠금이 아직 사용 중입니다")


def _assert_idle(row: sqlite3.Row) -> None:
    if row["worker_id"] is not None:
        raise SlotConflict("수동 회차 작업자가 아직 종료되지 않았습니다")


def upload_decision(
    data_dir: Path, run_id: str, now: datetime
) -> UploadDecision:
    """Claim one approved manual upload or hold every other manual state."""
    data_dir = Path(data_dir)
    database = data_dir / "videos.sqlite"
    if not database.exists():
        return "automatic"
    db = _connect(data_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        table = db.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'slot_reservations'"
        ).fetchone()
        if table is None:
            db.commit()
            return "automatic"
        row = _fetch(db, run_id)
        if row is None:
            db.commit()
            return "automatic"

        timestamp = _timestamp(now)
        if (
            row["state"] == "approved"
            and _as_kst(now) >= slot_window(run_id).upload_at
        ):
            changed = db.execute(
                """
                UPDATE slot_reservations
                SET state = 'uploading', stage = 'uploading', updated_at = ?
                WHERE run_id = ? AND mode = 'manual' AND state = 'approved'
                """,
                (timestamp, run_id),
            ).rowcount
            db.commit()
            return "approved" if changed == 1 else "hold"

        if (
            row["state"] == "review_ready"
            and _as_kst(now) >= slot_window(run_id).upload_at
        ):
            db.execute(
                """
                UPDATE slot_reservations
                SET state = 'held', stage = 'held', updated_at = ?
                WHERE run_id = ? AND state = 'review_ready'
                """,
                (timestamp, run_id),
            )
        db.commit()
        return "hold"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def approve_slot(data_dir: Path, run_id: str, now: datetime) -> dict:
    """Approve a completed artifact and tell the API whether to upload now."""
    db = _begin(data_dir)
    try:
        row = _fetch(db, run_id)
        if row is None:
            raise SlotConflict("수동 예약이 존재하지 않습니다")
        _assert_idle(row)
        if row["state"] not in {"review_ready", "held"}:
            raise SlotConflict("검수 대기 또는 보류 상태에서만 승인할 수 있습니다")
        current = _as_kst(now)
        action = "scheduled" if current < slot_window(run_id).upload_at else "immediate"
        timestamp = current.isoformat()
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'approved', stage = 'approved', approved_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (timestamp, timestamp, run_id),
        )
        result = _row_dict(_fetch(db, run_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    append_slot_event(data_dir, run_id, "approved", "info", "수동 영상을 승인했습니다")
    return {**result, "upload_action": action}


def reject_slot(
    data_dir: Path, run_id: str, reason: str, now: datetime
) -> dict:
    """Archive a completed artifact only after all workers have released it."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("반려 사유가 필요합니다")
    data_dir = Path(data_dir)
    _assert_no_live_global_lock(data_dir)
    db = _begin(data_dir)
    source: Path | None = None
    archive: Path | None = None
    moved = False
    try:
        row = _fetch(db, run_id)
        if row is None:
            raise SlotConflict("수동 예약이 존재하지 않습니다")
        _assert_idle(row)
        if row["state"] not in {"review_ready", "held"}:
            raise SlotConflict("검수 대기 또는 보류 상태에서만 반려할 수 있습니다")

        source = data_dir / "work" / run_id
        artifact_value = row["artifact_path"]
        if artifact_value is not None and Path(artifact_value).resolve() != source.resolve():
            raise SlotConflict("활성 산출물 경로가 회차 작업 경로와 다릅니다")
        if source.exists():
            archive = data_dir / "rejected" / f"{run_id}-attempt-{int(row['attempt'])}"
            if archive.exists():
                raise SlotConflict("같은 시도의 반려 산출물이 이미 보관되어 있습니다")
            archive.parent.mkdir(parents=True, exist_ok=True)
            source.replace(archive)
            moved = True

        timestamp = _timestamp(now)
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'rejected', stage = 'rejected', worker_id = NULL,
                rejected_at = ?, rejection_reason = ?, artifact_path = NULL,
                updated_at = ?
            WHERE run_id = ?
            """,
            (timestamp, reason.strip(), timestamp, run_id),
        )
        result = _row_dict(_fetch(db, run_id))
        db.commit()
    except Exception:
        db.rollback()
        if moved and source is not None and archive is not None and archive.exists():
            archive.replace(source)
        raise
    finally:
        db.close()
    append_slot_event(
        data_dir,
        run_id,
        "rejected",
        "info",
        "수동 영상을 반려하고 산출물을 보관했습니다",
        {"attempt": result["attempt"]},
    )
    return {**result, "archived_path": str(archive) if archive is not None else None}


def retry_slot(
    data_dir: Path, run_id: str, mode: RetryMode, now: datetime
) -> dict:
    """Start exactly one new attempt with the same checked topic or a new check."""
    if mode not in {"same_topic", "new_topic"}:
        raise ValueError("retry mode must be same_topic or new_topic")
    _assert_no_live_global_lock(Path(data_dir))
    db = _begin(data_dir)
    try:
        row = _fetch(db, run_id)
        if row is None:
            raise SlotConflict("수동 예약이 존재하지 않습니다")
        _assert_idle(row)
        if row["state"] not in {"failed", "rejected"}:
            raise SlotConflict("실패 또는 반려 상태에서만 재시도할 수 있습니다")

        target = "checking"
        check_result = None
        normalized_topic = None
        if mode == "same_topic":
            try:
                check_result = json.loads(row["check_result"])
                valid = (
                    isinstance(check_result, dict)
                    and check_result.get("status") == "reservable"
                    and isinstance(check_result.get("topic_payload"), dict)
                )
            except (TypeError, json.JSONDecodeError):
                valid = False
            if not valid:
                raise SlotConflict("같은 소재로 재시도할 유효한 검증 결과가 없습니다")
            target = "reserved"
            normalized_topic = row["normalized_topic"]

        timestamp = _timestamp(now)
        db.execute(
            """
            UPDATE slot_reservations
            SET state = ?, stage = ?, attempt = attempt + 1, worker_id = NULL,
                normalized_topic = ?, check_result = ?, approved_at = NULL,
                rejected_at = NULL, rejection_reason = NULL, artifact_path = NULL,
                updated_at = ?
            WHERE run_id = ?
            """,
            (
                target,
                target,
                normalized_topic,
                json.dumps(check_result, ensure_ascii=False, separators=(",", ":"))
                if check_result is not None
                else None,
                timestamp,
                run_id,
            ),
        )
        result = _row_dict(_fetch(db, run_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    append_slot_event(
        data_dir,
        run_id,
        target,
        "info",
        "수동 회차 재시도를 시작했습니다",
        {"attempt": result["attempt"], "mode": mode},
    )
    return result


def skip_slot(data_dir: Path, run_id: str, now: datetime) -> dict:
    """Finish a failed or rejected manual slot without uploading it."""
    _assert_no_live_global_lock(Path(data_dir))
    db = _begin(data_dir)
    try:
        row = _fetch(db, run_id)
        if row is None:
            raise SlotConflict("수동 예약이 존재하지 않습니다")
        _assert_idle(row)
        if row["state"] not in {"failed", "rejected"}:
            raise SlotConflict("실패 또는 반려 상태에서만 건너뛸 수 있습니다")
        timestamp = _timestamp(now)
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'skipped', stage = 'skipped', worker_id = NULL, updated_at = ?
            WHERE run_id = ?
            """,
            (timestamp, run_id),
        )
        result = _row_dict(_fetch(db, run_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    append_slot_event(data_dir, run_id, "skipped", "info", "수동 회차를 건너뛰었습니다")
    return result


def record_upload_result(
    data_dir: Path, run_id: str, result: dict, now: datetime
) -> dict:
    """Finish an owned manual upload claim from its uploader result."""
    uploaded = result.get("status") == "uploaded" or (
        result.get("status") == "skipped" and bool(result.get("video_id"))
    )
    target = "uploaded" if uploaded else "failed"
    db = _begin(data_dir)
    try:
        row = _fetch(db, run_id)
        if row is None or row["state"] != "uploading":
            raise SlotConflict("업로드 중인 수동 회차가 아닙니다")
        timestamp = _timestamp(now)
        db.execute(
            """
            UPDATE slot_reservations
            SET state = ?, stage = ?, worker_id = NULL, video_id = ?, updated_at = ?
            WHERE run_id = ? AND state = 'uploading'
            """,
            (target, target, result.get("video_id"), timestamp, run_id),
        )
        saved = _row_dict(_fetch(db, run_id))
        db.commit()
        return saved
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def fail_upload_claim(data_dir: Path, run_id: str, now: datetime) -> None:
    """Release an interrupted manual upload claim into a retryable failure state."""
    db = _begin(data_dir)
    try:
        row = _fetch(db, run_id)
        if row is not None and row["state"] == "uploading":
            timestamp = _timestamp(now)
            db.execute(
                """
                UPDATE slot_reservations
                SET state = 'failed', stage = 'uploading', worker_id = NULL, updated_at = ?
                WHERE run_id = ? AND state = 'uploading'
                """,
                (timestamp, run_id),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "approve_slot",
    "cleanup_rejected_artifacts",
    "reject_slot",
    "retry_slot",
    "skip_slot",
    "upload_decision",
]
