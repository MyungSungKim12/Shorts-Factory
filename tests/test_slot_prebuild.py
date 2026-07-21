import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import slot_prebuild
from app.services.slot_prebuild import next_scheduled_slot, promote_staging
from scripts import prepare_next_slot as command


KST = ZoneInfo("Asia/Seoul")


def _token():
    return "123456789" + ":" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcdefghi"


@pytest.mark.parametrize(
    ("hour", "expected_run_id", "expected_hour"),
    [
        (10, "20260721-1", 11),
        (12, "20260721-2", 17),
        (18, "20260721-3", 21),
        (22, "20260722-1", 11),
    ],
)
def test_next_scheduled_slot_uses_nearest_future_slot(
    hour: int, expected_run_id: str, expected_hour: int
) -> None:
    run_id, scheduled_at = next_scheduled_slot(
        datetime(2026, 7, 21, hour, 0, tzinfo=KST)
    )

    assert run_id == expected_run_id
    assert scheduled_at.hour == expected_hour
    assert scheduled_at.tzinfo == KST


def test_exact_slot_time_skips_the_running_slot() -> None:
    run_id, scheduled_at = next_scheduled_slot(
        datetime(2026, 7, 21, 11, 0, tzinfo=KST)
    )

    assert run_id == "20260721-2"
    assert scheduled_at.hour == 17


@pytest.mark.parametrize(
    ("hour", "slot", "run_id"),
    [(9, 1, "20260721-1"), (15, 2, "20260721-2"), (19, 3, "20260721-3")],
)
def test_prebuild_targets_explicit_same_day_slot(
    hour: int, slot: int, run_id: str
) -> None:
    selected_run_id, scheduled_at = slot_prebuild.scheduled_run(
        datetime(2026, 7, 21, hour, tzinfo=KST), slot
    )

    assert selected_run_id == run_id
    assert scheduled_at.date().isoformat() == "2026-07-21"


def test_expired_explicit_slot_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="이미 지난"):
        slot_prebuild.scheduled_run(datetime(2026, 7, 21, 17, tzinfo=KST), 2)


def _staging_package(data_dir: Path, staging_id: str = "sample") -> Path:
    staging = data_dir / "staging" / staging_id
    staging.mkdir(parents=True)
    script = {"title": "sample", "scenes": [], "cta": "구독과 좋아요"}
    script_bytes = json.dumps(script, ensure_ascii=False).encode("utf-8")
    (staging / "topic.json").write_text("{}", encoding="utf-8")
    (staging / "script.json").write_bytes(script_bytes)
    (staging / "output.mp4").write_bytes(b"video")
    (staging / "produce_log.json").write_text(
        json.dumps({"script_sha256": hashlib.sha256(script_bytes).hexdigest()}),
        encoding="utf-8",
    )
    return staging


def test_promote_staging_atomically_prepares_scheduled_work(tmp_path: Path) -> None:
    staging = _staging_package(tmp_path)
    quality = {"passed": True, "failures": [], "report": {"duration": 42.0}}
    scheduled_at = datetime(2026, 7, 21, 17, 0, tzinfo=KST)

    destination = promote_staging(
        tmp_path, "sample", "20260721-2", scheduled_at, quality
    )

    assert destination == tmp_path / "work" / "20260721-2"
    assert not staging.exists()
    assert (destination / "output.mp4").read_bytes() == b"video"
    prepared = json.loads((destination / "prepared.json").read_text(encoding="utf-8"))
    assert prepared["run_id"] == "20260721-2"
    assert prepared["scheduled_at"] == scheduled_at.isoformat()
    assert prepared["quality_gate"]["passed"] is True
    assert not list((tmp_path / "work").glob(".*.promoting-*"))


@pytest.mark.parametrize("failure", ["quality", "hash", "destination", "uploaded"])
def test_promote_rejects_unsafe_target_and_preserves_staging(
    tmp_path: Path, failure: str
) -> None:
    staging = _staging_package(tmp_path)
    quality = {"passed": True, "failures": []}
    if failure == "quality":
        quality = {"passed": False, "failures": ["duration_delta"]}
    elif failure == "hash":
        (staging / "script.json").write_text('{"title":"changed"}', encoding="utf-8")
    elif failure == "destination":
        target = tmp_path / "work" / "20260721-2"
        target.mkdir(parents=True)
        (target / "existing.txt").write_text("keep", encoding="utf-8")
    elif failure == "uploaded":
        with sqlite3.connect(tmp_path / "videos.sqlite") as db:
            db.execute("CREATE TABLE videos (video_id TEXT, date TEXT, status TEXT)")
            db.execute(
                "INSERT INTO videos VALUES (?, ?, ?)",
                ("youtube-id", "20260721-2", "uploaded"),
            )

    with pytest.raises(RuntimeError):
        promote_staging(
            tmp_path,
            "sample",
            "20260721-2",
            datetime(2026, 7, 21, 17, 0, tzinfo=KST),
            quality,
        )

    assert staging.exists()
    assert not list((tmp_path / "work").glob(".*.promoting-*"))


def test_prepare_command_builds_in_staging_and_never_uploads(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def fake_researcher(data_dir, staging_id, **kwargs):
        calls.append(("researcher", kwargs["work_root"]))

    def fake_writer(data_dir, staging_id, **kwargs):
        calls.append(("writer", kwargs["work_root"]))

    async def fake_producer(data_dir, staging_id, ffmpeg_path, **kwargs):
        calls.append(("producer", kwargs["work_root"]))

    monkeypatch.setattr(command, "run_researcher", fake_researcher)
    monkeypatch.setattr(command, "run_writer", fake_writer)
    monkeypatch.setattr(command, "run_producer", fake_producer)
    monkeypatch.setattr(
        command,
        "validate_upload_package",
        lambda *args: {"passed": True, "failures": []},
    )
    monkeypatch.setattr(
        command,
        "promote_staging",
        lambda data_dir, staging_id, run_id, scheduled_at, quality: data_dir
        / "work"
        / run_id,
    )

    result = command.prepare_next_slot(
        tmp_path,
        "ffmpeg",
        now_fn=lambda: datetime(2026, 7, 21, 12, 0, tzinfo=KST),
        use_lock=False,
    )

    assert calls == [
        ("researcher", "staging"),
        ("writer", "staging"),
        ("producer", "staging"),
    ]
    assert result["run_id"] == "20260721-2"
    assert result["scheduled_at"].hour == 17
    assert result["destination"] == tmp_path / "work" / "20260721-2"


@pytest.mark.parametrize("unsafe_target", ["destination", "uploaded"])
def test_explicit_prepare_rejects_unsafe_target_before_generation(
    tmp_path: Path, monkeypatch, unsafe_target: str
) -> None:
    run_id = "20260721-2"
    if unsafe_target == "destination":
        target = tmp_path / "work" / run_id
        target.mkdir(parents=True)
        (target / "prepared.json").write_text(
            json.dumps({"run_id": run_id, "quality_gate": {"passed": True}}),
            encoding="utf-8",
        )
    else:
        with sqlite3.connect(tmp_path / "videos.sqlite") as db:
            db.execute("CREATE TABLE videos (video_id TEXT, date TEXT, status TEXT)")
            db.execute(
                "INSERT INTO videos VALUES (?, ?, ?)",
                ("youtube-id", run_id, "uploaded"),
            )

    def generation_must_not_start(*args, **kwargs):
        raise AssertionError("generation must not start for an unsafe explicit slot")

    monkeypatch.setattr(command, "run_researcher", generation_must_not_start)

    with pytest.raises(RuntimeError):
        command.prepare_slot(
            tmp_path,
            "ffmpeg",
            2,
            now_fn=lambda: datetime(2026, 7, 21, 12, tzinfo=KST),
            use_lock=False,
        )


def test_fresh_cache_database_does_not_block_prebuild(tmp_path, monkeypatch):
    from app.services.cache_warmer import warm_verified_cache

    def cache_researcher(data_dir, run_id, **kwargs):
        from app.services.fact_cache import save_verified

        slot = int(run_id.rsplit("-", 1)[1])
        save_verified(data_dir, slot, {
            "topic": f"fresh topic {slot}",
            "ranking_size": 3,
            "items": [],
            "verification_method": "grounded_search",
        })

    warm_verified_cache(
        tmp_path,
        researcher=cache_researcher,
        now=datetime(2026, 7, 21, 6, 30),
    )
    with sqlite3.connect(tmp_path / "videos.sqlite") as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
        ).fetchone() is None

    monkeypatch.setattr(command, "run_researcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(command, "run_writer", lambda *args, **kwargs: None)

    async def fake_producer(*args, **kwargs):
        return None

    monkeypatch.setattr(command, "run_producer", fake_producer)
    monkeypatch.setattr(
        command, "validate_upload_package", lambda *args: {"passed": True, "failures": []}
    )
    monkeypatch.setattr(
        command,
        "promote_staging",
        lambda data_dir, staging_id, run_id, target_at, quality: data_dir / "work" / run_id,
    )

    result = command.prepare_slot(
        tmp_path,
        "ffmpeg",
        1,
        now_fn=lambda: datetime(2026, 7, 21, 9, tzinfo=KST),
        use_lock=False,
    )

    assert result["run_id"] == "20260721-1"


def test_cli_slot_uses_explicit_prepare(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        command,
        "prepare_slot",
        lambda data_dir, ffmpeg_path, slot, **kwargs: captured.update(
            data_dir=data_dir,
            ffmpeg_path=ffmpeg_path,
            slot=slot,
            **kwargs,
        )
        or {
            "destination": tmp_path / "work" / "20260721-3",
            "run_id": "20260721-3",
            "scheduled_at": datetime(2026, 7, 21, 21, tzinfo=KST),
        },
    )
    monkeypatch.setattr(command.sys, "argv", ["prepare_next_slot.py", "--slot", "3"])

    command.main()

    assert captured["slot"] == 3


def test_explicit_prepare_keeps_the_initial_target_after_rendering(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    scheduled_at = datetime(2026, 7, 21, 17, tzinfo=KST)

    monkeypatch.setattr(
        command,
        "scheduled_run",
        lambda now, slot: calls.append((now, slot)) or ("20260721-2", scheduled_at),
    )
    monkeypatch.setattr(command, "run_researcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(command, "run_writer", lambda *args, **kwargs: None)

    async def fake_producer(*args, **kwargs):
        return None

    monkeypatch.setattr(command, "run_producer", fake_producer)
    monkeypatch.setattr(
        command, "validate_upload_package", lambda *args: {"passed": True, "failures": []}
    )
    monkeypatch.setattr(
        command,
        "promote_staging",
        lambda data_dir, staging_id, run_id, target_at, quality: (
            calls.append((run_id, target_at)) or data_dir / "work" / run_id
        ),
    )

    result = command.prepare_slot(
        tmp_path,
        "ffmpeg",
        2,
        now_fn=lambda: datetime(2026, 7, 21, 12, tzinfo=KST),
        use_lock=False,
    )

    assert calls == [
        (datetime(2026, 7, 21, 12, tzinfo=KST), 2),
        ("20260721-2", scheduled_at),
    ]
    assert result["run_id"] == "20260721-2"


def test_prepare_cli_alerts_success_with_allowlisted_summary(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "work" / "20260721-3"
    destination.mkdir(parents=True)
    (destination / "script.json").write_text(
        json.dumps({"title": "approved title"}), encoding="utf-8"
    )
    (destination / "topic.json").write_text(
        json.dumps({"verification_method": "grounded_search"}), encoding="utf-8"
    )
    alerts = []
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        command,
        "prepare_next_slot",
        lambda *args, **kwargs: {
            "destination": destination,
            "run_id": "20260721-3",
            "scheduled_at": datetime(2026, 7, 21, 21, tzinfo=KST),
            "quality_gate": {"report": {"duration": 42.5}},
        },
    )
    monkeypatch.setattr(
        command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)), raising=False
    )
    monkeypatch.setattr(command.sys, "argv", ["prepare_next_slot.py"])

    command.main()

    assert alerts == [
        (
            (tmp_path, "prebuild:20260721-3:success"),
            {
                "text": (
                    "Prebuild succeeded\nrun_id: 20260721-3\ntitle: approved title"
                    "\nduration: 42.5\nverification_method: grounded_search"
                    "\nqc_passed: true"
                )
            },
        )
    ]


def test_prepare_cli_alerts_failure_without_changing_failure_result(
    tmp_path: Path, monkeypatch
) -> None:
    alerts = []
    token = _token()
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(command, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        command,
        "prepare_slot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(f"POST https://api.telegram.org/bot{token}/sendMessage failed")
        ),
    )
    monkeypatch.setattr(
        command, "send_alert", lambda *args, **kwargs: alerts.append((args, kwargs)), raising=False
    )
    monkeypatch.setattr(command.sys, "argv", ["prepare_next_slot.py", "--slot", "2"])

    with pytest.raises(RuntimeError, match="sendMessage failed"):
        command.main()

    assert len(alerts) == 1
    assert alerts[0][0][1].endswith(":failure")
    assert "stage: prebuild" in alerts[0][1]["text"]
    assert "error_category: prebuild_failed" in alerts[0][1]["text"]
    assert token not in alerts[0][1]["text"]
