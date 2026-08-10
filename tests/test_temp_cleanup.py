import json
import os
import time
from datetime import datetime, timezone

from app.services import temp_cleanup
from app.services.manual_slot_actions import cleanup_rejected_artifacts


def _age(path, seconds):
    timestamp = time.time() - seconds
    os.utime(path, (timestamp, timestamp))


def test_cleanup_removes_only_old_owned_prefix_directories(tmp_path, monkeypatch):
    old = tmp_path / "shorts-factory-old"
    old.mkdir()
    (old / "media.mp4").write_bytes(b"1234")
    _age(old / "media.mp4", 8 * 3600)
    _age(old, 8 * 3600)

    fresh = tmp_path / "shorts-factory-fresh"
    fresh.mkdir()
    _age(fresh, 60)

    unrelated = tmp_path / "unrelated-old"
    unrelated.mkdir()
    _age(unrelated, 8 * 3600)

    active = tmp_path / "shorts-factory-active"
    active.mkdir()
    (active / ".owner.json").write_text(json.dumps({"pid": os.getpid()}))
    _age(active / ".owner.json", 8 * 3600)
    _age(active, 8 * 3600)

    monkeypatch.setattr(temp_cleanup.tempfile, "gettempdir", lambda: str(tmp_path))
    report = temp_cleanup.cleanup_stale_temp_dirs(
        now=datetime.now(timezone.utc), max_age_seconds=6 * 3600
    )

    assert report == {"removed_dirs": 1, "removed_bytes": 4}
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert active.exists()


def test_mark_temp_owner_records_current_process(tmp_path):
    temp_cleanup.mark_temp_owner(tmp_path)
    owner = json.loads((tmp_path / ".owner.json").read_text())
    assert owner["pid"] == os.getpid()
    assert owner["created_at"]


def test_cleanup_rejected_artifacts_removes_only_expired_rejected_dirs(tmp_path):
    rejected = tmp_path / "rejected"
    expired = rejected / "20260801-1-attempt-1"
    fresh = rejected / "20260809-1-attempt-1"
    active = tmp_path / "work" / "20260801-1"
    ai_library = tmp_path / "ai-library" / "asset-1"
    for path in (expired, fresh, active, ai_library):
        path.mkdir(parents=True)
        (path / "artifact.bin").write_bytes(b"1234")
    old_timestamp = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    result = cleanup_rejected_artifacts(
        tmp_path, retention_days=7, now=datetime(2026, 8, 10, tzinfo=timezone.utc)
    )

    assert result == {"removed_dirs": 1, "removed_bytes": 4}
    assert not expired.exists()
    assert fresh.exists()
    assert active.exists()
    assert ai_library.exists()

