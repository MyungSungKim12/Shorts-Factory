import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services.slot_prebuild import next_scheduled_slot, promote_staging
from scripts import prepare_next_slot as command


KST = ZoneInfo("Asia/Seoul")


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
