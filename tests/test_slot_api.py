import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.slot_reservations import KST, create_check, init_slot_tables


client = TestClient(main.app)
TOKEN = {"X-Token": "secret"}


@pytest.fixture(autouse=True)
def configured_api(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    return tmp_path


def _run_id(days: int = 1, slot: int = 1) -> str:
    day = datetime.now(tz=KST).date() + timedelta(days=days)
    return f"{day:%Y%m%d}-{slot}"


def _checked_request() -> dict:
    return {
        "topic_input": "와우 신호의 실제 기록",
        "emphasis": "관측 당시 기록",
        "include": "공식 기관 출처",
        "exclude": "외계인이라고 단정",
        "reference_links": ["https://www.seti.org/wow-signal"],
    }


def _seed_manual(data_dir: Path, run_id: str, state: str, *, artifact: Path | None = None):
    init_slot_tables(data_dir)
    now = datetime.now(tz=KST).isoformat()
    day = datetime.strptime(run_id[:8], "%Y%m%d").date()
    production = datetime.combine(day, datetime.min.time(), tzinfo=KST).isoformat()
    upload = datetime.combine(day, datetime.max.time(), tzinfo=KST).isoformat()
    with sqlite3.connect(data_dir / "videos.sqlite") as db:
        db.execute(
            """
            INSERT INTO slot_reservations (
                run_id, mode, original_input, request_json, check_result, state,
                stage, attempt, production_at, upload_at, artifact_path,
                created_at, updated_at
            ) VALUES (?, 'manual', '와우 신호', '{}', ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                json.dumps({"status": "reservable", "raw_response": {"token": "leak"}}),
                state,
                state,
                production,
                upload,
                str(artifact) if artifact else None,
                now,
                now,
            ),
        )


def test_mutations_require_shared_dashboard_token(monkeypatch):
    run_id = _run_id()
    paths = [
        ("post", f"/api/slots/{run_id}/check-topic", _checked_request()),
        ("put", f"/api/slots/{run_id}/reservation", {"checked": True}),
        ("delete", f"/api/slots/{run_id}/reservation", None),
        ("post", f"/api/slots/{run_id}/approve", None),
        ("post", f"/api/slots/{run_id}/reject", {"reason": "수정 필요"}),
        ("post", f"/api/slots/{run_id}/retry", {"mode": "same_topic"}),
        ("post", f"/api/slots/{run_id}/skip", None),
    ]

    for method, path, body in paths:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, path

    monkeypatch.delenv("DASHBOARD_TOKEN")
    assert client.post(f"/api/slots/{run_id}/check-topic", json=_checked_request()).status_code == 503


def test_slot_list_exposes_auto_and_manual_cards(configured_api):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "reservable")
    day = datetime.strptime(run_id[:8], "%Y%m%d").date()

    response = client.get(f"/api/slots?date={day.isoformat()}")

    assert response.status_code == 200
    payload = response.json()
    assert [card["slot"] for card in payload["slots"]] == [1, 2, 3, 4]
    assert payload["slots"][0]["mode"] == "manual"
    assert payload["slots"][1]["mode"] == "auto"
    assert "artifact_path" not in payload["slots"][0]
    assert "request_json" not in payload["slots"][0]
    assert "raw_response" not in payload["slots"][0]["check_result"]


def test_check_topic_returns_202_and_persists_one_background_result(configured_api, monkeypatch):
    from app.routes import slots

    run_id = _run_id()
    calls = []

    def fake_check(data_dir, received_run_id, request):
        calls.append((Path(data_dir), received_run_id, request.topic_input))
        return {"status": "needs_input", "interpretations": ["A", "B"]}

    monkeypatch.setattr(slots, "check_requested_topic", fake_check)

    response = client.post(
        f"/api/slots/{run_id}/check-topic", json=_checked_request(), headers=TOKEN
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "run_id": run_id, "state": "checking"}
    assert calls == [(configured_api, run_id, "와우 신호의 실제 기록")]
    assert client.get(f"/api/slots/{run_id}").json()["state"] == "needs_input"


def test_check_topic_conflict_does_not_start_duplicate_worker(configured_api, monkeypatch):
    from app.routes import slots

    run_id = _run_id()
    create_check(configured_api, run_id, _checked_request(), datetime.now(tz=KST))

    def forbidden(*args, **kwargs):
        raise AssertionError("duplicate worker started")

    monkeypatch.setattr(slots, "check_requested_topic", forbidden)
    response = client.post(
        f"/api/slots/{run_id}/check-topic", json=_checked_request(), headers=TOKEN
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("run_id", "body"),
    [
        ("20260810-0", _checked_request()),
        ("20260810-5", _checked_request()),
        ("20260810-x", _checked_request()),
        (_run_id(), {**_checked_request(), "topic_input": " "}),
        (_run_id(), {**_checked_request(), "emphasis": "가" * 501}),
        (_run_id(), {**_checked_request(), "reference_links": ["http://example.com"]}),
        (
            _run_id(),
            {**_checked_request(), "reference_links": [f"https://example.com/{i}" for i in range(6)]},
        ),
    ],
)
def test_check_topic_rejects_invalid_run_id_and_body(run_id, body):
    assert client.post(f"/api/slots/{run_id}/check-topic", json=body, headers=TOKEN).status_code == 422


def test_reservation_maps_missing_conflict_and_cutoff(configured_api):
    missing = _run_id(slot=2)
    assert client.put(
        f"/api/slots/{missing}/reservation", json={"checked": True}, headers=TOKEN
    ).status_code == 404

    conflict = _run_id(slot=3)
    _seed_manual(configured_api, conflict, "reserved")
    assert client.put(
        f"/api/slots/{conflict}/reservation", json={"checked": True}, headers=TOKEN
    ).status_code == 409

    cutoff = _run_id(days=-1, slot=4)
    _seed_manual(configured_api, cutoff, "reservable")
    assert client.put(
        f"/api/slots/{cutoff}/reservation", json={"checked": True}, headers=TOKEN
    ).status_code == 422


def test_public_detail_and_events_never_expose_raw_payload(configured_api):
    from app.services.slot_reservations import append_slot_event

    run_id = _run_id()
    _seed_manual(configured_api, run_id, "reservable")
    append_slot_event(
        configured_api,
        run_id,
        "check",
        "info",
        "Authorization: Bearer hidden",
        {"provider_response": {"api_key": "hidden"}, "count": 2},
    )

    detail = client.get(f"/api/slots/{run_id}")
    events = client.get(f"/api/slots/{run_id}/events?after_id=0")

    assert detail.status_code == 200
    assert detail.json()["slot"] == 1
    assert "hidden" not in detail.text
    assert "raw_response" not in detail.text
    assert events.status_code == 200
    assert "hidden" not in events.text


def test_detail_and_events_return_404_for_missing_slot():
    run_id = _run_id(slot=4)
    assert client.get(f"/api/slots/{run_id}").status_code == 404
    assert client.get(f"/api/slots/{run_id}/events").status_code == 404


def test_video_download_requires_allowed_state_db_artifact_and_token(configured_api):
    run_id = _run_id()
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    (work / "output.mp4").write_bytes(b"video-bytes")
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    assert client.get(f"/api/slots/{run_id}/video").status_code == 401
    response = client.get(f"/api/slots/{run_id}/video", headers=TOKEN)
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"video-bytes"


def test_video_rejects_database_path_outside_work(configured_api):
    run_id = _run_id()
    outside = configured_api / "private"
    outside.mkdir()
    (outside / "output.mp4").write_bytes(b"secret")
    _seed_manual(configured_api, run_id, "review_ready", artifact=outside)

    assert client.get(f"/api/slots/{run_id}/video", headers=TOKEN).status_code == 404


def test_approve_after_upload_time_schedules_exactly_one_pipeline_call(configured_api, monkeypatch):
    from app.routes import slots

    run_id = _run_id(days=-1)
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)
    calls = []

    async def fake_pipeline(data_dir, ffmpeg_path, slot=None):
        calls.append((Path(data_dir), ffmpeg_path, slot))
        return {"success": True}

    monkeypatch.setattr(slots, "run_pipeline", fake_pipeline)
    response = client.post(f"/api/slots/{run_id}/approve", headers=TOKEN)

    assert response.status_code == 200
    assert response.json()["upload_action"] == "immediate"
    assert calls == [(configured_api, main.FFMPEG_PATH, 1)]
    assert client.post(f"/api/slots/{run_id}/approve", headers=TOKEN).status_code == 409
    assert len(calls) == 1


def test_approve_before_upload_time_waits_for_cron(configured_api, monkeypatch):
    from app.routes import slots

    run_id = _run_id(days=1)
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    async def forbidden(*args, **kwargs):
        raise AssertionError("pre-time approval must wait for cron")

    monkeypatch.setattr(slots, "run_pipeline", forbidden)
    response = client.post(f"/api/slots/{run_id}/approve", headers=TOKEN)

    assert response.status_code == 200
    assert response.json()["upload_action"] == "scheduled"


def test_reject_body_and_events_query_bounds_are_validated(configured_api):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "review_ready")

    assert client.post(
        f"/api/slots/{run_id}/reject", json={"reason": " "}, headers=TOKEN
    ).status_code == 422
    assert client.post(
        f"/api/slots/{run_id}/reject", json={"reason": "가" * 301}, headers=TOKEN
    ).status_code == 422
    assert client.get(f"/api/slots/{run_id}/events?after_id=-1").status_code == 422
    assert client.get(f"/api/slots/{run_id}/events?limit=101").status_code == 422


def test_invalid_calendar_run_id_is_rejected_without_server_error():
    assert client.get("/api/slots/20260230-1").status_code == 422


def test_new_topic_retry_validates_saved_request_before_state_change(configured_api):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "failed")

    response = client.post(
        f"/api/slots/{run_id}/retry", json={"mode": "new_topic"}, headers=TOKEN
    )

    assert response.status_code == 422
    with sqlite3.connect(configured_api / "videos.sqlite") as db:
        state = db.execute(
            "SELECT state FROM slot_reservations WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    assert state == "failed"


def test_detail_exposes_bounded_review_metadata_without_internal_paths(configured_api):
    run_id = _run_id()
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    (work / "script.json").write_text(
        json.dumps(
            {
                "format": "story",
                "title": "와우 신호의 기록",
                "description": "설명",
                "tags": ["우주"],
                "scenes": [{"n": 1, "narration": "첫 장면"}],
                "provider_response": {"token": "hidden"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work / "prepared.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "quality_gate": {"passed": True, "raw_response": "hidden"},
                "staging_id": "private-stage",
            }
        ),
        encoding="utf-8",
    )
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    response = client.get(f"/api/slots/{run_id}")

    assert response.status_code == 200
    assert response.json()["review"]["script"]["title"] == "와우 신호의 기록"
    assert "artifact_path" not in response.text
    assert "private-stage" not in response.text
    assert "hidden" not in response.text


def test_symlinked_work_root_outside_data_dir_is_never_trusted(
    configured_api, tmp_path_factory
):
    run_id = _run_id()
    outside = tmp_path_factory.mktemp("outside-work")
    external_artifact = outside / run_id
    external_artifact.mkdir()
    (external_artifact / "output.mp4").write_bytes(b"outside-video")
    (external_artifact / "script.json").write_text(
        json.dumps({"title": "outside-secret-title"}), encoding="utf-8"
    )
    os.symlink(outside, configured_api / "work", target_is_directory=True)
    _seed_manual(
        configured_api,
        run_id,
        "review_ready",
        artifact=configured_api / "work" / run_id,
    )

    detail = client.get(f"/api/slots/{run_id}")
    video = client.get(f"/api/slots/{run_id}/video", headers=TOKEN)

    assert detail.status_code == 200
    assert "review" not in detail.json()
    assert "outside-secret-title" not in detail.text
    assert video.status_code == 404


def test_detail_does_not_follow_symlinked_review_metadata(
    configured_api, tmp_path_factory
):
    run_id = _run_id()
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    outside = tmp_path_factory.mktemp("outside-metadata")
    external_script = outside / "script.json"
    external_script.write_text(
        json.dumps({"title": "outside-secret-title"}), encoding="utf-8"
    )
    os.symlink(external_script, work / "script.json")
    (work / "prepared.json").write_text(
        json.dumps({"run_id": run_id, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    response = client.get(f"/api/slots/{run_id}")

    assert response.status_code == 200
    assert "script" not in response.json()["review"]
    assert "outside-secret-title" not in response.text


def test_events_use_explicit_schema_and_defensive_secret_redaction(
    configured_api, monkeypatch
):
    from app.routes import slots

    run_id = _run_id()
    _seed_manual(configured_api, run_id, "reservable")
    monkeypatch.setattr(
        slots,
        "events_after",
        lambda *args, **kwargs: [
            {
                "id": 9,
                "run_id": run_id,
                "stage": "topic_check",
                "level": "info",
                "message": (
                    "access_token=message-secret Authorization: Bearer bearer-secret "
                    "raw_payload=provider-secret " + "x" * 600
                ),
                "metadata": {
                    "attempt": 2,
                    "status": "reservable",
                    "access_token": "metadata-secret",
                    "raw_payload": {"body": "provider-secret"},
                    "surprise": "not-public",
                },
                "created_at": "2026-08-10T10:00:00+09:00",
                "provider_response": {"secret": "raw"},
            }
        ],
    )

    response = client.get(f"/api/slots/{run_id}/events")

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert set(event) == {
        "id", "run_id", "stage", "level", "message", "metadata", "created_at"
    }
    assert event["metadata"] == {"attempt": 2, "status": "reservable"}
    assert len(event["message"]) <= 500
    assert "[redacted]]" not in event["message"]
    for secret in (
        "message-secret", "bearer-secret", "provider-secret",
        "metadata-secret", "not-public",
    ):
        assert secret not in response.text


@pytest.mark.parametrize(
    "grounding_error",
    [
        {"provider_error": "dict-secret"},
        ["list-secret"],
        "provider access_token=grounding-secret",
    ],
    ids=["dict", "list", "unknown-provider-text"],
)
def test_detail_omits_non_allowlisted_grounding_error(
    configured_api, grounding_error
):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "failed")
    with sqlite3.connect(configured_api / "videos.sqlite") as db:
        db.execute(
            "UPDATE slot_reservations SET check_result = ? WHERE run_id = ?",
            (
                json.dumps(
                    {
                        "status": "failed",
                        "reason": "grounding_invalid",
                        "grounding_error": grounding_error,
                    }
                ),
                run_id,
            ),
        )

    response = client.get(f"/api/slots/{run_id}")

    assert response.status_code == 200
    assert "grounding_error" not in response.json()["check_result"]
    for secret in ("dict-secret", "list-secret", "grounding-secret"):
        assert secret not in response.text
