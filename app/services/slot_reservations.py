"""SQLite-backed manual topic reservations for the four daily KST slots."""
from __future__ import annotations

import json
import re
import sqlite3
from uuid import uuid4
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
SLOT_TIMES = {
    1: (time(9, 0), time(11, 0)),
    2: (time(12, 0), time(14, 0)),
    3: (time(15, 0), time(17, 0)),
    4: (time(19, 0), time(21, 0)),
}
ACTIVE_STATES = {
    "locked", "researching", "writing", "producing", "quality_check", "uploading"
}
_CANCELLABLE_STATES = {"draft", "checking", "needs_input", "reservable", "reserved"}
ALLOWED_TRANSITIONS = {
    "draft": {"checking", "cancelled"},
    "checking": {"reservable", "needs_input", "failed"},
    "reservable": {"reserved", "checking", "cancelled"},
    "reserved": {"locked", "checking", "cancelled"},
    "locked": {"researching", "failed"},
    "researching": {"writing", "failed"},
    "writing": {"producing", "failed"},
    "producing": {"quality_check", "failed"},
    "quality_check": {"review_ready", "failed"},
    "review_ready": {"approved", "rejected", "held"},
    "held": {"approved", "rejected"},
    "approved": {"uploading"},
    "uploading": {"uploaded", "failed"},
    "failed": {"checking", "skipped"},
    "rejected": {"checking", "skipped"},
}

_RUN_ID = re.compile(r"^(\d{8})-([1-4])$")
_SENSITIVE_METADATA_KEY = re.compile(
    r"(?:token|key|credential|secret|password|authorization|cookie|session)",
    re.IGNORECASE,
)
_RAW_PROVIDER_PAYLOAD_KEY = re.compile(
    r"(?:raw|response|payload|body|headers|request)", re.IGNORECASE
)
_SECRET_VALUE = re.compile(
    r'''(?ix)
    (?P<label>
        ["']?(?:api[_-]?key|access[_-]?token|token|credential|secret|password|
        authorization|cookie|session)["']?\s*[:=]\s*
    )
    (?P<value>
        "(?:\\.|[^"])*" | '(?:\\.|[^'])*' | [^\s,;}\]]+
    )
    '''
)
_BOT_TOKEN = re.compile(r"(?<!\d)\d{5,15}:[A-Za-z0-9_-]{20,}")
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:basic|bearer)\s+[^\s,;}\]]+"
)
_EVENT_LEVELS = {"debug", "info", "warning", "error"}
_JSON_COLUMNS = {
    "include_constraints",
    "exclude_constraints",
    "reference_links",
    "request_json",
    "check_result",
}
_WRITABLE_FIELDS = {
    "stage",
    "attempt",
    "worker_id",
    "approved_at",
    "rejected_at",
    "rejection_reason",
    "artifact_path",
    "video_id",
    "normalized_topic",
    "include_constraints",
    "exclude_constraints",
    "reference_links",
    "request_json",
    "check_result",
}


class SlotConflict(RuntimeError):
    """Raised when a requested slot operation conflicts with persisted state."""


@dataclass(frozen=True)
class SlotWindow:
    run_id: str
    production_at: datetime
    upload_at: datetime


def _database_path(data_dir: Path) -> Path:
    return Path(data_dir) / "videos.sqlite"


def _connect(data_dir: Path) -> sqlite3.Connection:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_database_path(root), timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_slot_tables(data_dir: Path) -> None:
    """Create the reservation and append-only event tables when absent."""
    db = _connect(data_dir)
    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS slot_reservations (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                original_input TEXT,
                normalized_topic TEXT,
                include_constraints TEXT,
                exclude_constraints TEXT,
                reference_links TEXT,
                request_json TEXT NOT NULL,
                check_result TEXT,
                check_revision TEXT,
                state TEXT NOT NULL,
                stage TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                production_at TEXT NOT NULL,
                upload_at TEXT NOT NULL,
                reserved_at TEXT,
                locked_at TEXT,
                approved_at TEXT,
                rejected_at TEXT,
                rejection_reason TEXT,
                artifact_path TEXT,
                video_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                replacement_allowed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS slot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_slot_events_run_id_id
                ON slot_events (run_id, id);
            """
        )
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(slot_reservations)")
        }
        if "check_revision" not in columns:
            db.execute("ALTER TABLE slot_reservations ADD COLUMN check_revision TEXT")
        if "replacement_allowed" not in columns:
            db.execute(
                "ALTER TABLE slot_reservations "
                "ADD COLUMN replacement_allowed INTEGER NOT NULL DEFAULT 0"
            )
        db.commit()
    finally:
        db.close()


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def slot_window(run_id: str) -> SlotWindow:
    """Return the fixed KST production and upload times for one slot run ID."""
    match = _RUN_ID.fullmatch(run_id)
    if match is None:
        raise ValueError("run_id must have the YYYYMMDD-slot form")
    day = datetime.strptime(match.group(1), "%Y%m%d").date()
    slot = int(match.group(2))
    production_time, upload_time = SLOT_TIMES[slot]
    return SlotWindow(
        run_id=run_id,
        production_at=datetime.combine(day, production_time, tzinfo=KST),
        upload_at=datetime.combine(day, upload_time, tzinfo=KST),
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_json(value: str | None) -> object | None:
    return json.loads(value) if value else None


def _row_to_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    for column in _JSON_COLUMNS:
        result[column] = _decode_json(result[column])
    return result


def _fetch_reservation(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM slot_reservations WHERE run_id = ?", (run_id,)
    ).fetchone()


def _begin_immediate(data_dir: Path) -> sqlite3.Connection:
    init_slot_tables(data_dir)
    db = _connect(data_dir)
    db.execute("BEGIN IMMEDIATE")
    return db


def _ensure_input_open(run_id: str, now: datetime) -> SlotWindow:
    window = slot_window(run_id)
    if _as_kst(now) >= window.production_at:
        raise SlotConflict("입력 시간이 종료되었습니다")
    return window


def _request_values(request: dict) -> tuple[str, object, object, object]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    original_input = request.get("topic_input")
    if not isinstance(original_input, str) or not original_input.strip():
        raise ValueError("topic_input is required")
    return (
        original_input.strip(),
        request.get("include"),
        request.get("exclude"),
        request.get("reference_links"),
    )


def create_check(data_dir: Path, run_id: str, request: dict, now: datetime) -> dict:
    """Start (or restart) a manual topic check while the input window is open."""
    window = slot_window(run_id)
    original_input, include, exclude, reference_links = _request_values(request)
    timestamp = _as_kst(now).isoformat()
    revision = uuid4().hex
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        replacement = bool(
            row is not None
            and row["state"] == "draft"
            and row["worker_id"] is None
            and row["replacement_allowed"] == 1
        )
        if not replacement:
            _ensure_input_open(run_id, now)
        if row is None:
            db.execute(
                """
                INSERT INTO slot_reservations (
                    run_id, mode, original_input, include_constraints,
                    exclude_constraints, reference_links, request_json, check_revision,
                    state, stage,
                    attempt, production_at, upload_at, created_at, updated_at
                ) VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, 'checking', 'checking', 1, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    original_input,
                    _json(include),
                    _json(exclude),
                    _json(reference_links),
                    _json(request),
                    revision,
                    window.production_at.isoformat(),
                    window.upload_at.isoformat(),
                    timestamp,
                    timestamp,
                ),
            )
        else:
            if row["state"] not in {
                "draft", "reservable", "needs_input", "failed", "rejected"
            }:
                raise SlotConflict("slot cannot be checked from its current state")
            db.execute(
                """
                UPDATE slot_reservations
                SET original_input = ?, normalized_topic = NULL,
                    include_constraints = ?, exclude_constraints = ?, reference_links = ?,
                    request_json = ?, check_result = NULL, check_revision = ?,
                    state = 'checking',
                    stage = 'checking', worker_id = NULL, updated_at = ?,
                    attempt = attempt + CASE
                        WHEN state = 'draft' AND replacement_allowed = 1 THEN 0
                        ELSE 1
                    END
                WHERE run_id = ?
                """,
                (
                    original_input,
                    _json(include),
                    _json(exclude),
                    _json(reference_links),
                    _json(request),
                    revision,
                    timestamp,
                    run_id,
                ),
            )
        result = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_check_result(
    data_dir: Path,
    run_id: str,
    result: dict,
    now: datetime,
    *,
    revision: str,
) -> dict:
    """Persist a completed topic check and expose its next valid state."""
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    if not isinstance(revision, str) or not revision:
        raise ValueError("check revision is required")
    status = result.get("status")
    target = status if status in {"reservable", "needs_input", "failed"} else "failed"
    timestamp = _as_kst(now).isoformat()
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if (
            row is None
            or row["state"] != "checking"
            or row["check_revision"] != revision
        ):
            raise SlotConflict("check result requires matching active revision")
        normalized_topic = result.get("normalized_topic")
        changed = db.execute(
            """
            UPDATE slot_reservations
            SET normalized_topic = ?, check_result = ?, state = ?, stage = 'checked',
                updated_at = ?
            WHERE run_id = ? AND state = 'checking' AND check_revision = ?
            """,
            (
                normalized_topic if isinstance(normalized_topic, str) else None,
                _json(result),
                target,
                timestamp,
                run_id,
                revision,
            ),
        ).rowcount
        if changed != 1:
            raise SlotConflict("check result revision changed before save")
        saved = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return saved
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reserve_checked_topic(data_dir: Path, run_id: str, now: datetime) -> dict:
    """Reserve a successfully checked manual topic before production starts."""
    slot_window(run_id)
    timestamp = _as_kst(now).isoformat()
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if row is None or row["state"] != "reservable":
            raise SlotConflict("reservation requires reservable state")
        if row["replacement_allowed"] != 1:
            _ensure_input_open(run_id, now)
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'reserved', stage = 'reserved', reserved_at = ?,
                replacement_allowed = 0, updated_at = ?
            WHERE run_id = ?
            """,
            (timestamp, timestamp, run_id),
        )
        reserved = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return reserved
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_manual_reservation(
    data_dir: Path, run_id: str, now: datetime
) -> dict:
    """Atomically remove an inactive pre-cutoff manual gate back to automatic."""
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if row is None or row["mode"] != "manual":
            raise SlotConflict("reservation does not exist")
        _ensure_input_open(run_id, now)
        if row["worker_id"] is not None or row["state"] not in _CANCELLABLE_STATES:
            raise SlotConflict("reservation cannot be cancelled from its current state")
        deleted = db.execute(
            """
            DELETE FROM slot_reservations
            WHERE run_id = ? AND mode = 'manual' AND worker_id IS NULL
              AND state IN ('draft', 'checking', 'needs_input', 'reservable', 'reserved')
            """,
            (run_id,),
        ).rowcount
        if deleted != 1:
            raise SlotConflict("reservation changed while cancellation was requested")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    try:
        append_slot_event(
            data_dir,
            run_id,
            "cancelled",
            "info",
            "수동 회차 예약을 취소하고 자동 회차로 복원했습니다",
        )
    except Exception:
        pass
    return {
        "run_id": run_id,
        "cancelled": True,
        "mode": "auto",
        "state": "auto",
    }


def lock_reserved_slot(
    data_dir: Path, run_id: str, worker_id: str, now: datetime
) -> dict | None:
    """Atomically claim a due reserved slot for exactly one worker."""
    if not worker_id:
        raise ValueError("worker_id is required")
    window = slot_window(run_id)
    current = _as_kst(now)
    if current < window.production_at:
        return None
    timestamp = current.isoformat()
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if row is None or row["state"] != "reserved":
            db.commit()
            return None
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'locked', stage = 'locked', worker_id = ?, locked_at = ?, updated_at = ?
            WHERE run_id = ? AND state = 'reserved'
            """,
            (worker_id, timestamp, timestamp, run_id),
        )
        locked = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return locked
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def transition_slot(
    data_dir: Path,
    run_id: str,
    expected: set[str],
    target: str,
    now: datetime,
    **fields,
) -> dict:
    """Atomically apply one allowed state-machine edge to a reservation."""
    if not expected:
        raise ValueError("expected states are required")
    unknown = set(fields) - _WRITABLE_FIELDS
    if unknown:
        raise ValueError(f"unsupported reservation fields: {', '.join(sorted(unknown))}")
    timestamp = _as_kst(now).isoformat()
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if row is None:
            raise SlotConflict("reservation does not exist")
        current = row["state"]
        if current not in expected:
            raise SlotConflict("reservation is not in an expected state")
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise SlotConflict(f"transition from {current} to {target} is not allowed")
        if target in {"checking", "cancelled"}:
            _ensure_input_open(run_id, now)
        assignments = ["state = ?", "updated_at = ?"]
        values: list[object] = [target, timestamp]
        for name, value in fields.items():
            assignments.append(f"{name} = ?")
            values.append(_json(value) if name in _JSON_COLUMNS else value)
        values.append(run_id)
        db.execute(
            f"UPDATE slot_reservations SET {', '.join(assignments)} WHERE run_id = ?",
            values,
        )
        transitioned = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return transitioned
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def fail_owned_slot(
    data_dir: Path,
    run_id: str,
    worker_id: str,
    stage: str,
    now: datetime,
    *,
    artifact_path: str | None = None,
) -> dict:
    """Clear one owned active worker through an idempotent failure fallback."""
    if not worker_id:
        raise ValueError("worker_id is required")
    timestamp = _as_kst(now).isoformat()
    db = _begin_immediate(data_dir)
    try:
        row = _fetch_reservation(db, run_id)
        if row is None:
            raise SlotConflict("reservation does not exist")
        if row["state"] == "failed" and row["worker_id"] is None:
            if artifact_path is not None:
                db.execute(
                    """
                    UPDATE slot_reservations
                    SET artifact_path = ?, updated_at = ?
                    WHERE run_id = ? AND state = 'failed' AND worker_id IS NULL
                    """,
                    (artifact_path, timestamp, run_id),
                )
                row = _fetch_reservation(db, run_id)
            result = _row_to_dict(row)
            db.commit()
            return result
        if row["state"] not in ACTIVE_STATES or row["worker_id"] != worker_id:
            raise SlotConflict("active reservation is not owned by this worker")
        db.execute(
            """
            UPDATE slot_reservations
            SET state = 'failed', stage = ?, worker_id = NULL,
                artifact_path = COALESCE(?, artifact_path), updated_at = ?
            WHERE run_id = ? AND worker_id = ?
            """,
            (stage, artifact_path, timestamp, run_id, worker_id),
        )
        failed = _row_to_dict(_fetch_reservation(db, run_id))
        db.commit()
        return failed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _redact_text(value: str) -> str:
    redacted = _AUTHORIZATION_VALUE.sub("Authorization: [redacted]", value)
    redacted = _SECRET_VALUE.sub(r"\g<label>[redacted]", redacted)
    return _BOT_TOKEN.sub("[redacted]", redacted)


def _sanitize_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SENSITIVE_METADATA_KEY.search(str(key))
            or _RAW_PROVIDER_PAYLOAD_KEY.search(str(key))
            else _sanitize_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def append_slot_event(
    data_dir: Path,
    run_id: str,
    stage: str,
    level: str,
    message: str,
    metadata: dict | None = None,
) -> int:
    """Append one bounded, credential-safe slot event and return its event ID."""
    if level not in _EVENT_LEVELS:
        raise ValueError("unsupported event level")
    if not isinstance(message, str):
        raise ValueError("event message must be a string")
    safe_metadata = _sanitize_metadata(metadata or {})
    metadata_json = _json(safe_metadata)
    if len(metadata_json.encode("utf-8")) > 4096:
        metadata_json = _json({"truncated": True})
    safe_message = _redact_text(message)[:500]
    init_slot_tables(data_dir)
    db = _connect(data_dir)
    try:
        cursor = db.execute(
            """
            INSERT INTO slot_events (run_id, stage, level, message, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                stage,
                level,
                safe_message,
                metadata_json,
                datetime.now(tz=KST).isoformat(),
            ),
        )
        db.commit()
        return int(cursor.lastrowid)
    finally:
        db.close()


def events_after(
    data_dir: Path, run_id: str, after_id: int, limit: int = 100
) -> list[dict]:
    """Return the next bounded page from a slot's append-only event stream."""
    if not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit == 0:
        return []
    init_slot_tables(data_dir)
    db = _connect(data_dir)
    try:
        rows = db.execute(
            """
            SELECT id, run_id, stage, level, message, metadata, created_at
            FROM slot_events
            WHERE run_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (run_id, after_id, max(1, min(limit, 100))),
        ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]
    finally:
        db.close()


def list_slot_cards(data_dir: Path, day: date, now: datetime) -> list[dict]:
    """Return four dashboard-ready cards, preserving automatic mode when absent."""
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    init_slot_tables(data_dir)
    run_ids = [f"{day:%Y%m%d}-{slot}" for slot in SLOT_TIMES]
    placeholders = ", ".join("?" for _ in run_ids)
    db = _connect(data_dir)
    try:
        rows = {
            row["run_id"]: _row_to_dict(row)
            for row in db.execute(
                f"SELECT * FROM slot_reservations WHERE run_id IN ({placeholders})", run_ids
            ).fetchall()
        }
    finally:
        db.close()

    current = _as_kst(now)
    cards: list[dict] = []
    for run_id in run_ids:
        window = slot_window(run_id)
        stored = rows.get(run_id)
        card = {
            "run_id": run_id,
            "slot": int(run_id.rsplit("-", 1)[1]),
            "mode": "auto",
            "state": "auto",
            "production_at": window.production_at,
            "upload_at": window.upload_at,
            "input_open": current < window.production_at,
        }
        if stored is not None:
            card.update(stored)
            card["input_open"] = current < window.production_at
        cards.append(card)
    return cards
