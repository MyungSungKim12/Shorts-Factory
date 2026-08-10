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
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["run_id"] == run_id
    assert payload["state"] == "checking"
    assert payload["attempt"] == 1
    assert isinstance(payload["revision"], str) and payload["revision"]
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


def test_cancel_restores_auto_card_and_allows_fresh_manual_check(
    configured_api, monkeypatch
):
    from app.routes import slots

    run_id = _run_id()
    _seed_manual(configured_api, run_id, "reserved")
    day = datetime.strptime(run_id[:8], "%Y%m%d").date()

    cancelled = client.delete(
        f"/api/slots/{run_id}/reservation", headers=TOKEN
    )
    cards = client.get(f"/api/slots?date={day.isoformat()}").json()["slots"]

    assert cancelled.status_code == 200
    assert cancelled.json()["mode"] == "auto"
    assert cancelled.json()["state"] == "auto"
    assert cards[0]["mode"] == "auto"
    assert cards[0]["state"] == "auto"

    monkeypatch.setattr(
        slots,
        "check_requested_topic",
        lambda *args, **kwargs: {
            "status": "needs_input",
            "interpretations": ["A", "B"],
        },
    )
    recheck = client.post(
        f"/api/slots/{run_id}/check-topic",
        json=_checked_request(),
        headers=TOKEN,
    )

    assert recheck.status_code == 202
    assert client.get(f"/api/slots/{run_id}").json()["state"] == "needs_input"


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
    assert client.get(f"/api/slots/{run_id}/events").json() == {
        "run_id": run_id,
        "events": [],
    }


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

    async def fake_pipeline(data_dir, ffmpeg_path, slot=None, *, run_id_override=None):
        calls.append((Path(data_dir), ffmpeg_path, slot, run_id_override))
        return {"success": True}

    monkeypatch.setattr(slots, "run_pipeline", fake_pipeline)
    response = client.post(f"/api/slots/{run_id}/approve", headers=TOKEN)

    assert response.status_code == 200
    assert response.json()["upload_action"] == "immediate"
    assert calls == [(configured_api, main.FFMPEG_PATH, 1, run_id)]
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


def test_new_topic_retry_ignores_old_request_and_returns_fresh_draft(configured_api):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "failed")

    response = client.post(
        f"/api/slots/{run_id}/retry", json={"mode": "new_topic"}, headers=TOKEN
    )

    assert response.status_code == 200
    assert response.json()["state"] == "draft"
    with sqlite3.connect(configured_api / "videos.sqlite") as db:
        state, request_json, original_input = db.execute(
            "SELECT state, request_json, original_input FROM slot_reservations "
            "WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert state == "draft"
    assert json.loads(request_json) == {}
    assert original_input is None


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


def test_automatic_slot_events_return_empty_page_instead_of_404(configured_api):
    run_id = _run_id(slot=4)

    response = client.get(f"/api/slots/{run_id}/events")

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "events": []}


def test_unauthenticated_slot_payload_omits_inputs_and_redacts_embedded_secrets(
    configured_api,
):
    run_id = _run_id()
    _seed_manual(configured_api, run_id, "reservable")
    with sqlite3.connect(configured_api / "videos.sqlite") as db:
        db.execute(
            "UPDATE slot_reservations SET include_constraints = ?, "
            "exclude_constraints = ?, reference_links = ?, check_result = ? "
            "WHERE run_id = ?",
            (
                json.dumps("include api_key=input-secret"),
                json.dumps("exclude access_token=input-token"),
                json.dumps(["https://example.test/private?X-Amz-Signature=signed-secret"]),
                json.dumps(
                    {
                        "status": "reservable",
                        "normalized_topic": "safe title",
                        "sources": [
                            {
                                "source_url": "https://example.test/public?api_key=url-secret",
                                "note": "access_token=embedded-secret",
                            }
                        ],
                    }
                ),
                run_id,
            ),
        )

    listing = client.get(
        f"/api/slots?date={datetime.strptime(run_id[:8], '%Y%m%d').date()}"
    )
    detail = client.get(f"/api/slots/{run_id}")

    for response in (listing, detail):
        assert response.status_code == 200
        text = response.text
        assert "original_input" not in text
        assert "include_constraints" not in text
        assert "exclude_constraints" not in text
        assert "reference_links" not in text
        for secret in (
            "input-secret", "input-token", "signed-secret", "url-secret",
            "embedded-secret", "X-Amz-Signature",
        ):
            assert secret not in text
    assert "https://example.test/public" in detail.text

    authenticated = client.get(f"/api/slots/{run_id}", headers=TOKEN)
    assert authenticated.status_code == 200
    assert authenticated.json()["original_input"]


def test_review_exposes_sanitized_actual_provenance_distinct_from_preflight(
    configured_api,
):
    run_id = _run_id()
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    (work / "script.json").write_text(
        json.dumps({"title": "review title"}), encoding="utf-8"
    )
    (work / "prepared.json").write_text(
        json.dumps({"run_id": run_id, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    (work / "produce_log.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "provider": "wikimedia_image",
                        "source_url": "https://commons.wikimedia.org/wiki/File:One?token=signed",
                    },
                    {
                        "provider": "pexels",
                        "source_url": "https://pexels.com/video/2?X-Amz-Signature=hidden",
                    },
                    {
                        "provider": "pexels",
                        "source_url": "https://pexels.com/video/2?duplicate=yes",
                        "note": "api_key=producer-secret",
                    },
                ],
                "provider_response": {"access_token": "raw-secret"},
            }
        ),
        encoding="utf-8",
    )
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    response = client.get(f"/api/slots/{run_id}")

    assert response.status_code == 200
    actual = response.json()["review"]["actual_sources"]
    assert actual == {
        "item_count": 3,
        "unique_source_count": 2,
        "types": {"pexels": 2, "wikimedia_image": 1},
        "public_urls": [
            "https://commons.wikimedia.org/wiki/File:One",
            "https://pexels.com/video/2",
        ],
    }
    for secret in ("signed", "hidden", "producer-secret", "raw-secret"):
        assert secret not in response.text


def test_review_never_reads_symlinked_produce_log(configured_api, tmp_path_factory):
    run_id = _run_id()
    work = configured_api / "work" / run_id
    work.mkdir(parents=True)
    (work / "script.json").write_text(
        json.dumps({"title": "review title"}), encoding="utf-8"
    )
    (work / "prepared.json").write_text(
        json.dumps({"run_id": run_id, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    outside = tmp_path_factory.mktemp("outside-produce-log") / "produce_log.json"
    outside.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "provider": "private",
                        "source_url": "https://private.test/?token=symlink-secret",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    os.symlink(outside, work / "produce_log.json")
    _seed_manual(configured_api, run_id, "review_ready", artifact=work)

    response = client.get(f"/api/slots/{run_id}")

    assert response.status_code == 200
    assert "actual_sources" not in response.json()["review"]
    assert "symlink-secret" not in response.text


def test_post_production_new_topic_retry_accepts_replacement_and_prebuilds_once(
    configured_api, monkeypatch
):
    from app.routes import slots

    run_id = _run_id(days=-1)
    _seed_manual(configured_api, run_id, "failed")
    calls = []
    checked_inputs = []

    def fake_check(data_dir, received_run_id, request):
        checked_inputs.append(request.topic_input)
        return {
            "status": "reservable",
            "reservable": True,
            "normalized_topic": "replacement topic",
        }

    monkeypatch.setattr(slots, "check_requested_topic", fake_check)

    def fake_prebuild(data_dir, ffmpeg_path, received_run_id):
        calls.append(received_run_id)
        with sqlite3.connect(Path(data_dir) / "videos.sqlite") as db:
            db.execute(
                "UPDATE slot_reservations SET state = 'review_ready', "
                "stage = 'review_ready', worker_id = NULL WHERE run_id = ?",
                (received_run_id,),
            )
        return {"state": "review_ready"}

    monkeypatch.setattr(slots, "run_manual_prebuild", fake_prebuild)

    retry = client.post(
        f"/api/slots/{run_id}/retry", json={"mode": "new_topic"}, headers=TOKEN
    )
    assert retry.status_code == 200
    assert retry.json()["state"] == "draft"
    assert calls == []
    assert checked_inputs == []

    checked = client.post(
        f"/api/slots/{run_id}/check-topic",
        json={**_checked_request(), "topic_input": "replacement topic"},
        headers=TOKEN,
    )
    assert checked.status_code == 202
    assert checked_inputs == ["replacement topic"]
    assert client.get(f"/api/slots/{run_id}").json()["state"] == "reservable"

    reserved = client.put(
        f"/api/slots/{run_id}/reservation", json={"checked": True}, headers=TOKEN
    )
    assert reserved.status_code == 200
    assert calls == [run_id]
    assert client.get(f"/api/slots/{run_id}").json()["state"] == "review_ready"


def test_same_topic_retry_schedules_one_immediate_prebuild_after_release(
    configured_api, monkeypatch
):
    from app.routes import slots
    from app.services import manual_slot_actions

    run_id = _run_id(days=-1)
    _seed_manual(configured_api, run_id, "failed")
    monkeypatch.setattr(
        manual_slot_actions,
        "validate_reservable_check_result",
        lambda value: {"status": "reservable", "normalized_topic": "same topic"},
    )
    calls = []

    def fake_prebuild(data_dir, ffmpeg_path, received_run_id):
        calls.append(received_run_id)
        with sqlite3.connect(Path(data_dir) / "videos.sqlite") as db:
            db.execute(
                "UPDATE slot_reservations SET state = 'review_ready', "
                "stage = 'review_ready', worker_id = NULL WHERE run_id = ?",
                (received_run_id,),
            )
        return {"state": "review_ready"}

    monkeypatch.setattr(slots, "run_manual_prebuild", fake_prebuild)

    response = client.post(
        f"/api/slots/{run_id}/retry", json={"mode": "same_topic"}, headers=TOKEN
    )

    assert response.status_code == 200
    assert response.json()["state"] == "reserved"
    assert calls == [run_id]
    assert client.get(f"/api/slots/{run_id}").json()["state"] == "review_ready"
