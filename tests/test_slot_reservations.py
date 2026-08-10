import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pytest

from app.services.slot_reservations import (
    ALLOWED_TRANSITIONS,
    SlotConflict,
    append_slot_event,
    create_check,
    events_after,
    init_slot_tables,
    list_slot_cards,
    lock_reserved_slot,
    reserve_checked_topic,
    save_check_result,
    slot_window,
    transition_slot,
)
from app.services import slot_reservations


KST = ZoneInfo("Asia/Seoul")


def _save_current_check(data_dir, run_id, result, now):
    with sqlite3.connect(Path(data_dir) / "videos.sqlite") as db:
        revision = db.execute(
            "SELECT check_revision FROM slot_reservations WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    return save_check_result(data_dir, run_id, result, now, revision=revision)


def kst(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=KST)


def checked_result() -> dict:
    return {
        "status": "reservable",
        "normalized_topic": "검증된 수동 소재",
        "topic_payload": {"format": "story", "topic": "검증된 수동 소재"},
        "visual": {"level": "high", "reservable": True},
    }


def seed_reserved(data_dir: Path, run_id: str = "20260810-1") -> None:
    create_check(data_dir, run_id, {"topic_input": "검증된 수동 소재"}, kst(8, 40))
    _save_current_check(data_dir, run_id, checked_result(), kst(8, 41))
    reserve_checked_topic(data_dir, run_id, kst(8, 42))


def test_slot_one_window_uses_kst_cutoff_and_upload_time() -> None:
    window = slot_window("20260810-1")

    assert window.production_at.isoformat() == "2026-08-10T09:00:00+09:00"
    assert window.upload_at.isoformat() == "2026-08-10T11:00:00+09:00"


def test_reservation_is_rejected_at_production_cutoff(tmp_path: Path) -> None:
    create_check(tmp_path, "20260810-1", {"topic_input": "세종"}, kst(8, 50))
    _save_current_check(tmp_path, "20260810-1", checked_result(), kst(8, 51))

    with pytest.raises(SlotConflict, match="입력 시간이 종료되었습니다"):
        reserve_checked_topic(tmp_path, "20260810-1", kst(9, 0))


def test_only_one_worker_can_lock_reserved_slot(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    locked = lock_reserved_slot(tmp_path, "20260810-1", "worker-a", kst(9))

    assert locked["state"] == "locked"
    assert locked["worker_id"] == "worker-a"
    assert lock_reserved_slot(tmp_path, "20260810-1", "worker-b", kst(9)) is None


def test_check_result_persists_manual_request_and_normalized_topic(tmp_path: Path) -> None:
    created = create_check(
        tmp_path,
        "20260810-2",
        {
            "topic_input": "고대 도시",
            "include": ["발굴"],
            "exclude": ["전쟁"],
            "reference_links": ["https://example.test/source"],
        },
        kst(10),
    )
    saved = _save_current_check(tmp_path, "20260810-2", checked_result(), kst(10, 1))

    assert created["mode"] == "manual"
    assert created["state"] == "checking"
    assert created["include_constraints"] == ["발굴"]
    assert saved["state"] == "reservable"
    assert saved["normalized_topic"] == "검증된 수동 소재"
    assert saved["check_result"] == checked_result()


def test_needs_input_can_start_a_clarified_check(tmp_path: Path) -> None:
    create_check(tmp_path, "20260810-2", {"topic_input": "세종"}, kst(10))
    _save_current_check(
        tmp_path,
        "20260810-2",
        {"status": "needs_input", "normalized_topic": "세종대왕 또는 세종시"},
        kst(10, 1),
    )

    restarted = create_check(
        tmp_path,
        "20260810-2",
        {"topic_input": "조선 세종대왕"},
        kst(10, 2),
    )

    assert restarted["state"] == "checking"
    assert restarted["original_input"] == "조선 세종대왕"
    assert restarted["attempt"] == 2


def test_needs_input_is_rechecked_only_by_the_dedicated_operation(tmp_path: Path) -> None:
    create_check(tmp_path, "20260810-2", {"topic_input": "세종"}, kst(10))
    _save_current_check(tmp_path, "20260810-2", {"status": "needs_input"}, kst(10, 1))

    assert "needs_input" not in ALLOWED_TRANSITIONS
    with pytest.raises(SlotConflict, match="not allowed"):
        transition_slot(
            tmp_path, "20260810-2", {"needs_input"}, "checking", kst(10, 2)
        )


def test_active_check_cannot_be_replaced_without_its_result(tmp_path: Path) -> None:
    create_check(tmp_path, "20260810-2", {"topic_input": "첫 소재"}, kst(10))

    with pytest.raises(SlotConflict, match="cannot be checked"):
        create_check(tmp_path, "20260810-2", {"topic_input": "두 번째 소재"}, kst(10, 1))


def test_transition_requires_expected_state_and_allowed_edge(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    with pytest.raises(SlotConflict, match="expected state"):
        transition_slot(
            tmp_path,
            "20260810-1",
            {"checking"},
            "locked",
            kst(8, 50),
        )
    with pytest.raises(SlotConflict, match="not allowed"):
        transition_slot(
            tmp_path,
            "20260810-1",
            {"reserved"},
            "uploaded",
            kst(8, 50),
        )

    transitioned = transition_slot(
        tmp_path,
        "20260810-1",
        {"reserved"},
        "locked",
        kst(9),
        worker_id="worker-a",
        stage="research",
    )

    assert transitioned["state"] == "locked"
    assert transitioned["stage"] == "research"
    assert transitioned["worker_id"] == "worker-a"


def test_cancel_transition_is_rejected_at_production_cutoff(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    with pytest.raises(SlotConflict, match="입력 시간이 종료되었습니다"):
        transition_slot(
            tmp_path, "20260810-1", {"reserved"}, "cancelled", kst(9)
        )

    assert lock_reserved_slot(tmp_path, "20260810-1", "worker-a", kst(9))["state"] == "locked"


def test_cancel_manual_reservation_removes_gate_and_preserves_audit(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    result = slot_reservations.cancel_manual_reservation(
        tmp_path, "20260810-1", kst(8, 50)
    )

    assert result == {
        "run_id": "20260810-1",
        "cancelled": True,
        "mode": "auto",
        "state": "auto",
    }
    cards = list_slot_cards(tmp_path, date(2026, 8, 10), kst(8, 51))
    assert cards[0]["mode"] == "auto"
    assert cards[0]["state"] == "auto"
    audit = events_after(tmp_path, "20260810-1", 0)
    assert audit[-1]["stage"] == "cancelled"
    assert audit[-1]["metadata"] == {}


@pytest.mark.parametrize(
    ("state", "worker_id"),
    [
        ("locked", "worker"),
        ("producing", "worker"),
        ("review_ready", None),
        ("approved", None),
        ("uploading", "worker"),
    ],
)
def test_cancel_manual_reservation_rejects_active_or_produced_states(
    tmp_path: Path, state: str, worker_id: str | None
) -> None:
    seed_reserved(tmp_path)
    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        db.execute(
            "UPDATE slot_reservations SET state = ?, worker_id = ? WHERE run_id = ?",
            (state, worker_id, "20260810-1"),
        )

    with pytest.raises(SlotConflict):
        slot_reservations.cancel_manual_reservation(
            tmp_path, "20260810-1", kst(8, 50)
        )

    assert list_slot_cards(tmp_path, date(2026, 8, 10), kst(8, 51))[0]["mode"] == "manual"


def test_cancel_manual_reservation_is_rejected_at_cutoff(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    with pytest.raises(SlotConflict, match="입력 시간이 종료되었습니다"):
        slot_reservations.cancel_manual_reservation(tmp_path, "20260810-1", kst(9))

    assert lock_reserved_slot(tmp_path, "20260810-1", "worker", kst(9))["state"] == "locked"


def test_cutoff_cancel_cannot_compete_with_due_worker_lock(tmp_path: Path) -> None:
    seed_reserved(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as workers:
        lock = workers.submit(lock_reserved_slot, tmp_path, "20260810-1", "worker-a", kst(9))
        cancel = workers.submit(
            transition_slot,
            tmp_path,
            "20260810-1",
            {"reserved"},
            "cancelled",
            kst(9),
        )

    assert lock.result()["state"] == "locked"
    with pytest.raises(SlotConflict):
        cancel.result()


def test_list_slot_cards_keeps_unreserved_slots_automatic(tmp_path: Path) -> None:
    seed_reserved(tmp_path, "20260810-2")

    cards = list_slot_cards(tmp_path, date(2026, 8, 10), kst(10))

    assert [card["run_id"] for card in cards] == [
        "20260810-1",
        "20260810-2",
        "20260810-3",
        "20260810-4",
    ]
    assert cards[0]["mode"] == "auto"
    assert cards[1]["mode"] == "manual"
    assert cards[1]["state"] == "reserved"
    assert cards[1]["input_open"] is True
    assert cards[0]["production_at"].isoformat() == "2026-08-10T09:00:00+09:00"


def test_events_are_bounded_redacted_and_incremental(tmp_path: Path) -> None:
    secret = "api_key=super-secret-value"
    first_id = append_slot_event(
        tmp_path,
        "20260810-1",
        "checking",
        "warning",
        f"{secret} " + "x" * 600,
        {"credential": "do-not-store", "detail": "y" * 5000},
    )
    second_id = append_slot_event(
        tmp_path, "20260810-1", "checking", "info", "ready", {"safe": True}
    )

    events = events_after(tmp_path, "20260810-1", first_id, limit=1)

    assert len(events) == 1
    assert events[0]["id"] == second_id
    assert events[0]["run_id"] == "20260810-1"
    assert events[0]["stage"] == "checking"
    assert events[0]["level"] == "info"
    assert events[0]["message"] == "ready"
    assert events[0]["metadata"] == {"safe": True}
    assert events[0]["created_at"]
    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        message, metadata = db.execute(
            "SELECT message, metadata FROM slot_events WHERE id = ?", (first_id,)
        ).fetchone()
    assert "super-secret-value" not in message
    assert "do-not-store" not in metadata
    assert len(message) <= 500
    assert len(metadata.encode("utf-8")) <= 4096
    assert json.loads(metadata)["truncated"] is True


def test_events_redact_authorization_headers(tmp_path: Path) -> None:
    event_id = append_slot_event(
        tmp_path,
        "20260810-1",
        "checking",
        "error",
        "Authorization: Bearer private-access-token",
        {"authorization": "Bearer private-access-token"},
    )

    event = events_after(tmp_path, "20260810-1", event_id - 1)[0]

    assert "private-access-token" not in event["message"]
    assert event["metadata"]["authorization"] == "[redacted]"


def test_events_redact_quoted_json_basic_auth_and_provider_payloads(tmp_path: Path) -> None:
    secret_values = {
        "json-secret",
        "basic-secret",
        "nested-secret",
        "provider-secret",
        "header-secret",
    }
    event_id = append_slot_event(
        tmp_path,
        "20260810-1",
        "checking",
        "error",
        'provider returned {"api_key":"json-secret"}; Authorization: Basic basic-secret',
        {
            "detail": '{"api_key":"nested-secret"}',
            "provider_response": {"token": "provider-secret"},
            "headers": {"X-Api-Key": "header-secret"},
            "safe": "visible",
        },
    )

    event = events_after(tmp_path, "20260810-1", event_id - 1)[0]
    stored = json.dumps(event, ensure_ascii=False)

    assert not any(secret in stored for secret in secret_values)
    assert event["metadata"]["provider_response"] == "[redacted]"
    assert event["metadata"]["headers"] == "[redacted]"
    assert event["metadata"]["safe"] == "visible"


def test_events_after_zero_limit_returns_no_rows(tmp_path: Path) -> None:
    append_slot_event(tmp_path, "20260810-1", "checking", "info", "first")

    assert events_after(tmp_path, "20260810-1", 0, limit=0) == []


def test_event_level_is_restricted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported event level"):
        append_slot_event(tmp_path, "20260810-1", "checking", "critical", "nope")


def test_init_slot_tables_is_idempotent(tmp_path: Path) -> None:
    init_slot_tables(tmp_path)
    init_slot_tables(tmp_path)

    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"slot_reservations", "slot_events"} <= names


def test_stale_check_result_cannot_overwrite_cancelled_and_recreated_check(
    tmp_path: Path,
) -> None:
    first = create_check(
        tmp_path, "20260810-2", {"topic_input": "first topic"}, kst(10)
    )
    release_old_worker = Event()
    with ThreadPoolExecutor(max_workers=1) as workers:
        old_save = workers.submit(
            lambda: (
                release_old_worker.wait(),
                save_check_result(
                    tmp_path,
                    "20260810-2",
                    {"status": "reservable", "normalized_topic": "stale"},
                    kst(10, 3),
                    revision=first["check_revision"],
                ),
            )[-1]
        )
        slot_reservations.cancel_manual_reservation(
            tmp_path, "20260810-2", kst(10, 1)
        )
        second = create_check(
            tmp_path,
            "20260810-2",
            {"topic_input": "replacement topic"},
            kst(10, 2),
        )
        release_old_worker.set()

        assert first["check_revision"] != second["check_revision"]
        with pytest.raises(SlotConflict, match="revision"):
            old_save.result()

    saved = save_check_result(
        tmp_path,
        "20260810-2",
        {"status": "needs_input", "interpretations": ["new"]},
        kst(10, 4),
        revision=second["check_revision"],
    )
    assert saved["state"] == "needs_input"
    assert saved["check_result"]["interpretations"] == ["new"]
