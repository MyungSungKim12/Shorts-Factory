from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agents import orchestrator
from app.services.manual_slot_actions import (
    approve_slot,
    cleanup_rejected_artifacts,
    reject_slot,
    retry_slot,
    skip_slot,
    upload_decision,
)
from app.services.slot_reservations import (
    SlotConflict,
    create_check,
    reserve_checked_topic,
    save_check_result,
    transition_slot,
)
from app.services import slot_reservations


KST = ZoneInfo("Asia/Seoul")
RUN_ID = "20260810-1"


def _save_current_check(data_dir, run_id, result, now):
    with sqlite3.connect(Path(data_dir) / "videos.sqlite") as db:
        revision = db.execute(
            "SELECT check_revision FROM slot_reservations WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    return save_check_result(data_dir, run_id, result, now, revision=revision)


def kst(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=KST)


def checked_result() -> dict:
    topic = manual_topic_payload()
    return {
        "status": "reservable",
        "reservable": True,
        "normalized_topic": topic["topic"],
        "verification_method": "grounded_search",
        "safety": {"allowed": True, "reason": "일반 과학 설명"},
        "sources": [
            {"source": fact["source"], "source_url": fact["source_url"]}
            for fact in topic["facts"]
        ],
        "topic_payload": topic,
        "visual": {"level": "high", "reservable": True, "candidate_count": 1},
    }


def manual_topic_payload() -> dict:
    return {
        "format": "story",
        "topic": "1977년 단 한 번 관측된 와우 신호의 정체",
        "category": "science_mystery",
        "hook_angle": "72초 동안 포착된 강한 신호는 다시 나타나지 않았다",
        "target_keyword": "Wow signal",
        "core_question": "와우 신호는 어디에서 왔는가",
        "interest_score": 27,
        "selection_reason": "한 번뿐인 우주 관측 기록이다",
        "facts": [
            {
                "claim": "오하이오 주립대 전파망원경이 신호를 기록했다",
                "value": "1977년 8월 15일 약 72초 동안 관측됐다",
                "source": "Ohio State University",
                "source_url": "https://osu.edu/wow-signal",
            },
            {
                "claim": "신호는 수소선 주파수 부근에서 기록됐다",
                "value": "천문학적 전파 관측 후보로 분석됐다",
                "source": "SETI Institute",
                "source_url": "https://www.seti.org/wow-signal",
            },
        ],
        "visual_plan": [
            {
                "beat": "hook",
                "keywords": ["Wow signal printout", "Big Ear radio telescope"],
            }
        ],
        "visual_identity": {
            "exact_queries": ["exact:Wow signal"],
            "safe_fallbacks": ["radio telescope night"],
            "required_exact": True,
        },
        "verification_method": "grounded_search",
        "verified_at": "2026-08-10T01:23:45+00:00",
    }


def story_script() -> dict:
    roles = ["hook", "context", "problem", "mechanism", "mechanism", "payoff", "close"]
    return {
        "format": "story",
        "title": "단 한 번 포착된 와우 신호",
        "description": "1977년 관측 기록을 살펴봅니다.",
        "tags": ["와우신호"],
        "hook": "72초 동안 나타난 신호는 무엇이었을까요",
        "scenes": [
            {
                "n": index,
                "role": role,
                "narration": f"와우 신호 관측 기록의 {index}번째 단서를 확인합니다.",
                "visuals": ["Wow signal printout", "radio telescope night"],
                "duration_sec": 8,
                "emphasis": [],
            }
            for index, role in enumerate(roles, start=1)
        ],
        "cta": "구독과 좋아요 부탁드립니다.",
        "total_duration_sec": 56,
    }


def seed_review_ready_slot(
    data_dir: Path,
    run_id: str = RUN_ID,
    *,
    state: str = "review_ready",
    with_artifact: bool = False,
) -> None:
    create_check(data_dir, run_id, {"topic_input": "검증된 수동 소재"}, kst(8, 40))
    _save_current_check(data_dir, run_id, checked_result(), kst(8, 41))
    reserve_checked_topic(data_dir, run_id, kst(8, 42))
    transition_slot(
        data_dir, run_id, {"reserved"}, "locked", kst(9), worker_id="worker"
    )
    for source, target in (
        ("locked", "researching"),
        ("researching", "writing"),
        ("writing", "producing"),
        ("producing", "quality_check"),
        ("quality_check", "review_ready"),
    ):
        fields = {"worker_id": None} if target == "review_ready" else {}
        transition_slot(data_dir, run_id, {source}, target, kst(9, 1), **fields)
    if state == "held":
        transition_slot(data_dir, run_id, {"review_ready"}, "held", kst(11))
    if with_artifact:
        artifact = data_dir / "work" / run_id
        artifact.mkdir(parents=True)
        (artifact / "output.mp4").write_bytes(b"review-video")
        with sqlite3.connect(data_dir / "videos.sqlite") as db:
            db.execute(
                "UPDATE slot_reservations SET artifact_path = ? WHERE run_id = ?",
                (str(artifact), run_id),
            )


def read_slot(data_dir: Path, run_id: str = RUN_ID) -> dict:
    with sqlite3.connect(data_dir / "videos.sqlite") as db:
        db.row_factory = sqlite3.Row
        return dict(
            db.execute(
                "SELECT * FROM slot_reservations WHERE run_id = ?", (run_id,)
            ).fetchone()
        )


def test_unapproved_manual_slot_is_held_without_auto_fallback(tmp_path, monkeypatch):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    calls: list[str] = []
    for name in ("run_researcher", "run_writer", "run_producer", "run_uploader"):
        monkeypatch.setattr(
            orchestrator, name, lambda *args, _name=name, **kwargs: calls.append(_name)
        )

    result = asyncio.run(
        orchestrator.run_pipeline(
            tmp_path, "ffmpeg", slot=1, run_id_override=RUN_ID
        )
    )

    assert result["success"] is True
    assert result["stages"]["uploader"] == {
        "status": "skipped",
        "reason": "manual_review_required",
    }
    assert calls == []
    assert (tmp_path / "work" / RUN_ID / "output.mp4").read_bytes() == b"review-video"


def test_approval_before_upload_waits_for_scheduler(tmp_path):
    seed_review_ready_slot(tmp_path)

    result = approve_slot(tmp_path, RUN_ID, kst(10, 30))

    assert result["state"] == "approved"
    assert result["upload_action"] == "scheduled"


def test_approval_after_upload_requests_one_immediate_upload(tmp_path):
    seed_review_ready_slot(tmp_path, state="held")

    result = approve_slot(tmp_path, RUN_ID, kst(11, 5))

    assert result["state"] == "approved"
    assert result["upload_action"] == "immediate"
    with pytest.raises(SlotConflict):
        approve_slot(tmp_path, RUN_ID, kst(11, 6))


def test_upload_decision_atomically_claims_approved_slot_once(tmp_path):
    seed_review_ready_slot(tmp_path)
    approve_slot(tmp_path, RUN_ID, kst(10, 30))

    assert upload_decision(tmp_path, RUN_ID, kst(10, 59)) == "hold"
    assert read_slot(tmp_path)["state"] == "approved"
    assert upload_decision(tmp_path, RUN_ID, kst(11)) == "approved"
    assert upload_decision(tmp_path, RUN_ID, kst(11)) == "hold"
    assert read_slot(tmp_path)["state"] == "uploading"


def test_cancelled_preproduction_slot_returns_upload_decision_to_automatic(tmp_path):
    create_check(tmp_path, RUN_ID, {"topic_input": "검증된 수동 소재"}, kst(8, 40))
    _save_current_check(tmp_path, RUN_ID, checked_result(), kst(8, 41))
    reserve_checked_topic(tmp_path, RUN_ID, kst(8, 42))

    slot_reservations.cancel_manual_reservation(tmp_path, RUN_ID, kst(8, 50))

    assert upload_decision(tmp_path, RUN_ID, kst(11)) == "automatic"


def test_approved_manual_package_reuses_artifacts_and_records_uploaded_state(
    tmp_path, monkeypatch
):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    work = tmp_path / "work" / RUN_ID
    topic = manual_topic_payload()
    script = story_script()
    (work / "topic.json").write_text(
        json.dumps(topic, ensure_ascii=False), encoding="utf-8"
    )
    (work / "script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    (work / "produce_log.json").write_text(
        json.dumps(
            {
                "script_sha256": hashlib.sha256(
                    (work / "script.json").read_bytes()
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )
    (work / "prepared.json").write_text(
        json.dumps({"run_id": RUN_ID, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    approve_slot(tmp_path, RUN_ID, kst(11, 5))
    calls = []

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return kst(11, 5) if tz is not None else kst(11, 5).replace(tzinfo=None)

    monkeypatch.setattr(orchestrator, "datetime", FixedDatetime)
    monkeypatch.setenv("CONTENT_FORMAT", "ranking")
    for name in ("run_researcher", "run_writer", "run_producer"):
        monkeypatch.setattr(
            orchestrator,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"승인된 패키지에서 {_name}가 호출됨"
            ),
        )
    monkeypatch.setattr(
        orchestrator,
        "run_uploader",
        lambda *args, **kwargs: calls.append("uploader")
        or {"status": "uploaded", "video_id": "manual-video"},
    )
    monkeypatch.setattr(
        "app.agents.analyst.run_analyst", lambda *args, **kwargs: {"insight": "ok"}
    )

    result = asyncio.run(orchestrator.run_pipeline(tmp_path, "ffmpeg", slot=1))

    assert result["success"] is True
    assert calls == ["uploader"]
    assert read_slot(tmp_path)["state"] == "uploaded"
    assert read_slot(tmp_path)["video_id"] == "manual-video"


def test_approved_manual_script_uses_saved_story_format_when_global_is_ranking(
    tmp_path, monkeypatch
):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    work = tmp_path / "work" / RUN_ID
    invalid_story = story_script()
    invalid_story["scenes"][0]["role"] = "context"
    (work / "topic.json").write_text(
        json.dumps(manual_topic_payload(), ensure_ascii=False), encoding="utf-8"
    )
    (work / "script.json").write_text(
        json.dumps(invalid_story, ensure_ascii=False), encoding="utf-8"
    )
    (work / "produce_log.json").write_text(
        json.dumps(
            {
                "script_sha256": hashlib.sha256(
                    (work / "script.json").read_bytes()
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )
    (work / "prepared.json").write_text(
        json.dumps({"run_id": RUN_ID, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    approve_slot(tmp_path, RUN_ID, kst(11, 5))

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return kst(11, 5) if tz is not None else kst(11, 5).replace(tzinfo=None)

    monkeypatch.setattr(orchestrator, "datetime", FixedDatetime)
    monkeypatch.setenv("CONTENT_FORMAT", "ranking")
    monkeypatch.setattr(
        orchestrator,
        "run_uploader",
        lambda *args, **kwargs: pytest.fail("유효하지 않은 story 대본이 업로더에 도달함"),
    )

    with pytest.raises(RuntimeError, match="수동 대본 파일이 유효하지 않습니다"):
        asyncio.run(orchestrator.run_pipeline(tmp_path, "ffmpeg", slot=1))


def test_reject_archives_artifact_and_allows_same_topic_retry(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)

    result = reject_slot(tmp_path, RUN_ID, "대본 수정 필요", kst(10, 20))

    archived = Path(result["archived_path"])
    assert archived == tmp_path / "rejected" / f"{RUN_ID}-attempt-1"
    assert archived.is_dir()
    assert (archived / "output.mp4").read_bytes() == b"review-video"
    assert not (tmp_path / "work" / RUN_ID).exists()
    assert result["state"] == "rejected"
    assert read_slot(tmp_path)["artifact_path"] is None

    retried = retry_slot(tmp_path, RUN_ID, "same_topic", kst(10, 21))
    assert retried["state"] == "reserved"
    assert retried["attempt"] == 2
    assert json.loads(read_slot(tmp_path)["check_result"])["status"] == "reservable"


def test_reject_resets_old_artifact_mtime_to_rejection_time(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    source = tmp_path / "work" / RUN_ID
    old_timestamp = kst(0).timestamp() - 30 * 86400
    os.utime(source, (old_timestamp, old_timestamp))

    result = reject_slot(tmp_path, RUN_ID, "새로 반려", kst(10, 20))

    archived = Path(result["archived_path"])
    assert archived.stat().st_mtime == pytest.approx(kst(10, 20).timestamp(), abs=2)
    assert cleanup_rejected_artifacts(tmp_path, 7, kst(10, 20)) == {
        "removed_dirs": 0,
        "removed_bytes": 0,
    }
    assert archived.exists()


def test_new_topic_retry_clears_previous_check_fields(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    reject_slot(tmp_path, RUN_ID, "다른 소재 필요", kst(10, 20))

    retried = retry_slot(tmp_path, RUN_ID, "new_topic", kst(10, 21))

    assert retried["state"] == "draft"
    assert retried["attempt"] == 2
    assert retried["normalized_topic"] is None
    assert retried["check_result"] is None
    assert retried["replacement_allowed"] == 1


def test_same_topic_retry_requires_a_valid_last_check(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    reject_slot(tmp_path, RUN_ID, "재검증 필요", kst(10, 20))
    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        db.execute(
            "UPDATE slot_reservations SET check_result = ? WHERE run_id = ?",
            (json.dumps({"status": "failed"}), RUN_ID),
        )

    with pytest.raises(SlotConflict, match="검증"):
        retry_slot(tmp_path, RUN_ID, "same_topic", kst(10, 21))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda result: result.update(normalized_topic=""),
        lambda result: result["visual"].update(reservable=False),
        lambda result: result["topic_payload"].update(
            verification_method="model_memory"
        ),
        lambda result: result["topic_payload"].update(
            facts=result["topic_payload"]["facts"][:1]
        ),
        lambda result: result["topic_payload"].update(category="INVALID CATEGORY"),
    ],
)
def test_same_topic_retry_revalidates_complete_saved_check(tmp_path, mutate):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    reject_slot(tmp_path, RUN_ID, "재검증 필요", kst(10, 20))
    malformed = checked_result()
    mutate(malformed)
    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        db.execute(
            "UPDATE slot_reservations SET normalized_topic = ?, check_result = ? "
            "WHERE run_id = ?",
            (
                malformed.get("normalized_topic"),
                json.dumps(malformed, ensure_ascii=False),
                RUN_ID,
            ),
        )

    with pytest.raises(SlotConflict, match="검증"):
        retry_slot(tmp_path, RUN_ID, "same_topic", kst(10, 21))


def test_reject_refuses_a_live_global_lock(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    lock = tmp_path / "recovery" / "pipeline.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "run_id": RUN_ID}), encoding="utf-8"
    )

    with pytest.raises(SlotConflict, match="잠금"):
        reject_slot(tmp_path, RUN_ID, "대본 수정 필요", kst(10, 20))

    assert (tmp_path / "work" / RUN_ID).is_dir()
    assert read_slot(tmp_path)["state"] == "review_ready"


def test_skip_finishes_rejected_slot_without_upload(tmp_path):
    seed_review_ready_slot(tmp_path, with_artifact=True)
    reject_slot(tmp_path, RUN_ID, "이번 회차 중단", kst(10, 20))

    result = skip_slot(tmp_path, RUN_ID, kst(10, 21))

    assert result["state"] == "skipped"


def test_skip_clears_post_cutoff_replacement_authorization(tmp_path):
    seed_review_ready_slot(tmp_path)
    reject_slot(tmp_path, RUN_ID, "replacement rejected", kst(10, 20))
    retry_slot(tmp_path, RUN_ID, "new_topic", kst(10, 21))
    created = create_check(
        tmp_path,
        RUN_ID,
        {"topic_input": "replacement that fails validation"},
        kst(10, 22),
    )
    save_check_result(
        tmp_path,
        RUN_ID,
        {"status": "failed", "reason": "not enough evidence"},
        kst(10, 23),
        revision=created["check_revision"],
    )

    result = skip_slot(tmp_path, RUN_ID, kst(10, 24))

    assert result["state"] == "skipped"
    assert result["replacement_allowed"] == 0


@pytest.mark.parametrize("action", ["approve", "reject", "retry", "skip"])
def test_committed_action_succeeds_when_audit_event_write_fails(
    tmp_path, monkeypatch, action
):
    from app.services import manual_slot_actions

    seed_review_ready_slot(tmp_path, with_artifact=action == "reject")
    if action in {"retry", "skip"}:
        reject_slot(tmp_path, RUN_ID, "준비", kst(10, 20))
    monkeypatch.setattr(
        manual_slot_actions,
        "append_slot_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("event unavailable")),
    )

    if action == "approve":
        result = approve_slot(tmp_path, RUN_ID, kst(10, 20))
        expected = "approved"
    elif action == "reject":
        result = reject_slot(tmp_path, RUN_ID, "반려", kst(10, 20))
        expected = "rejected"
    elif action == "retry":
        result = retry_slot(tmp_path, RUN_ID, "same_topic", kst(10, 21))
        expected = "reserved"
    else:
        result = skip_slot(tmp_path, RUN_ID, kst(10, 21))
        expected = "skipped"

    assert result["state"] == expected
    assert read_slot(tmp_path)["state"] == expected


def test_overdue_exact_run_override_never_claims_todays_same_slot(
    tmp_path, monkeypatch
):
    yesterday = RUN_ID
    today = "20260811-1"
    seed_review_ready_slot(tmp_path, yesterday, with_artifact=True)
    seed_review_ready_slot(tmp_path, today, with_artifact=True)
    work = tmp_path / "work" / yesterday
    (work / "topic.json").write_text(
        json.dumps(manual_topic_payload(), ensure_ascii=False), encoding="utf-8"
    )
    (work / "script.json").write_text(
        json.dumps(story_script(), ensure_ascii=False), encoding="utf-8"
    )
    (work / "produce_log.json").write_text(
        json.dumps(
            {
                "script_sha256": hashlib.sha256(
                    (work / "script.json").read_bytes()
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )
    (work / "prepared.json").write_text(
        json.dumps({"run_id": yesterday, "quality_gate": {"passed": True}}),
        encoding="utf-8",
    )
    approved_today = datetime(2026, 8, 11, 0, 5, tzinfo=KST)
    approve_slot(tmp_path, yesterday, approved_today)

    class NextDayDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 8, 11, 0, 5, tzinfo=KST)
            return current if tz is not None else current.replace(tzinfo=None)

    monkeypatch.setattr(orchestrator, "datetime", NextDayDatetime)
    monkeypatch.setattr(
        orchestrator,
        "run_uploader",
        lambda data_dir, run_id: {"status": "uploaded", "video_id": run_id},
    )
    monkeypatch.setattr(
        "app.agents.analyst.run_analyst", lambda *args, **kwargs: {"insight": "ok"}
    )

    result = asyncio.run(
        orchestrator.run_pipeline(
            tmp_path, "ffmpeg", slot=1, run_id_override=yesterday
        )
    )

    assert result["date"] == yesterday
    assert read_slot(tmp_path, yesterday)["state"] == "uploaded"
    assert read_slot(tmp_path, today)["state"] == "review_ready"


def test_exact_run_override_rejects_slot_mismatch(tmp_path):
    with pytest.raises(ValueError, match="slot"):
        asyncio.run(
            orchestrator.run_pipeline(
                tmp_path, "ffmpeg", slot=2, run_id_override=RUN_ID
            )
        )


def test_reject_reconciles_interruption_after_artifact_move(
    tmp_path, monkeypatch
):
    from app.services import manual_slot_actions

    seed_review_ready_slot(tmp_path, with_artifact=True)
    original = manual_slot_actions._commit_rejected_state

    def terminate_after_move(*args, **kwargs):
        raise SystemExit("simulated termination")

    monkeypatch.setattr(
        manual_slot_actions, "_commit_rejected_state", terminate_after_move
    )
    with pytest.raises(SystemExit, match="simulated termination"):
        reject_slot(tmp_path, RUN_ID, "replace it", kst(10, 20))

    assert not (tmp_path / "work" / RUN_ID).exists()
    assert read_slot(tmp_path)["state"] == "review_ready"

    monkeypatch.setattr(manual_slot_actions, "_commit_rejected_state", original)
    reconciled = manual_slot_actions.reconcile_pending_reject(tmp_path, RUN_ID)

    assert reconciled["complete"] is True
    assert read_slot(tmp_path)["state"] == "rejected"
    archive = tmp_path / "rejected" / f"{RUN_ID}-attempt-1"
    assert (archive / "output.mp4").read_bytes() == b"review-video"


def test_reject_reconciles_interruption_after_marker_before_move(
    tmp_path, monkeypatch
):
    from app.services import manual_slot_actions

    seed_review_ready_slot(tmp_path, with_artifact=True)
    original = manual_slot_actions._move_reject_artifact

    def terminate_before_move(*args, **kwargs):
        raise SystemExit("simulated termination")

    monkeypatch.setattr(
        manual_slot_actions, "_move_reject_artifact", terminate_before_move
    )
    with pytest.raises(SystemExit, match="simulated termination"):
        reject_slot(tmp_path, RUN_ID, "replace it", kst(10, 20))

    assert (tmp_path / "work" / RUN_ID / "output.mp4").read_bytes() == b"review-video"
    assert read_slot(tmp_path)["state"] == "review_ready"

    monkeypatch.setattr(manual_slot_actions, "_move_reject_artifact", original)
    reconciled = manual_slot_actions.reconcile_pending_reject(tmp_path, RUN_ID)

    assert reconciled["complete"] is True
    assert read_slot(tmp_path)["state"] == "rejected"
    assert (
        tmp_path / "rejected" / f"{RUN_ID}-attempt-1" / "output.mp4"
    ).read_bytes() == b"review-video"


def test_reject_reconciles_interruption_after_database_commit(
    tmp_path, monkeypatch
):
    from app.services import manual_slot_actions

    seed_review_ready_slot(tmp_path, with_artifact=True)
    original = manual_slot_actions._clear_reject_marker

    def terminate_before_marker_cleanup(*args, **kwargs):
        raise SystemExit("simulated termination")

    monkeypatch.setattr(
        manual_slot_actions, "_clear_reject_marker", terminate_before_marker_cleanup
    )
    with pytest.raises(SystemExit, match="simulated termination"):
        reject_slot(tmp_path, RUN_ID, "replace it", kst(10, 20))

    assert read_slot(tmp_path)["state"] == "rejected"
    monkeypatch.setattr(manual_slot_actions, "_clear_reject_marker", original)

    reconciled = manual_slot_actions.reconcile_pending_reject(tmp_path, RUN_ID)
    assert reconciled["complete"] is True
    assert not (tmp_path / "recovery" / "manual-reject" / f"{RUN_ID}.json").exists()
    assert (
        tmp_path / "rejected" / f"{RUN_ID}-attempt-1" / "output.mp4"
    ).read_bytes() == b"review-video"
