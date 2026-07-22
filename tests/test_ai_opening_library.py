from datetime import datetime, timezone
from pathlib import Path

from scripts.run_daily import cleanup_old_work


def _asset_files(data_dir: Path, asset_id: str) -> dict[str, str]:
    asset_dir = data_dir / "media" / "ai_openings" / asset_id
    asset_dir.mkdir(parents=True)
    paths = {}
    for name in ("reference.jpg", "master.mp4", "opening.mp4"):
        path = asset_dir / name
        path.write_bytes(b"asset")
        paths[name] = str(path)
    return paths


def _register(library, data_dir: Path, asset_id: str, subject: str, status="ready"):
    paths = _asset_files(data_dir, asset_id)
    return library.register_asset(metadata={
        "asset_id": asset_id,
        "subject_key": subject,
        "reuse_scope": "exact_subject",
        "status": status,
        "reference_path": paths["reference.jpg"],
        "master_path": paths["master.mp4"],
        "opening_path": paths["opening.mp4"],
        "source_url": "https://commons.wikimedia.org/wiki/File:Subject.jpg",
        "license": "CC BY-SA 4.0",
        "model": "veo-3.1-fast-generate-001",
        "prompt": "Preserve the real subject with only a slow camera push.",
    })


def test_work_cleanup_never_deletes_permanent_ai_library(tmp_path):
    old_work = tmp_path / "work" / "20200101-1"
    permanent = tmp_path / "media" / "ai_openings" / "asset-1"
    old_work.mkdir(parents=True)
    permanent.mkdir(parents=True)
    (permanent / "master.mp4").write_bytes(b"keep")

    cleanup_old_work(tmp_path, keep_days=7)

    assert not old_work.exists()
    assert (permanent / "master.mp4").read_bytes() == b"keep"


def test_library_reuses_only_same_exact_subject(tmp_path):
    from app.services.ai_opening_library import AiOpeningLibrary

    library = AiOpeningLibrary(tmp_path)
    _register(library, tmp_path, "asset-1", "richat-structure")

    assert library.find_reusable_asset("richat-structure").asset_id == "asset-1"
    assert library.find_reusable_asset("eye-of-sahara-lookalike") is None


def test_library_avoids_recent_asset_when_older_choice_exists(tmp_path):
    from app.services.ai_opening_library import AiOpeningLibrary

    library = AiOpeningLibrary(tmp_path)
    _register(library, tmp_path, "recent", "richat-structure")
    _register(library, tmp_path, "older", "richat-structure")
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    library.mark_asset_used("recent", "20260722-1", now=now)

    selected = library.find_reusable_asset(
        "richat-structure", cooldown_days=14, now=now
    )

    assert selected.asset_id == "older"


def test_library_reuses_recent_asset_instead_of_forcing_new_generation(tmp_path):
    from app.services.ai_opening_library import AiOpeningLibrary

    library = AiOpeningLibrary(tmp_path)
    _register(library, tmp_path, "only", "richat-structure")
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    library.mark_asset_used("only", "20260722-1", now=now)

    selected = library.find_reusable_asset(
        "richat-structure", cooldown_days=14, now=now
    )

    assert selected.asset_id == "only"


def test_rejected_asset_remains_on_disk_but_is_not_reusable(tmp_path):
    from app.services.ai_opening_library import AiOpeningLibrary

    library = AiOpeningLibrary(tmp_path)
    asset = _register(
        library, tmp_path, "rejected-1", "richat-structure", status="rejected"
    )

    assert asset.master_path.exists()
    assert library.find_reusable_asset(asset.subject_key) is None


def test_reference_frame_mismatch_is_rejected(tmp_path, monkeypatch):
    from app.services import ai_opening_library

    reference = tmp_path / "reference.jpg"
    master = tmp_path / "master.mp4"
    reference.write_bytes(b"image")
    master.write_bytes(b"video")
    monkeypatch.setattr(
        ai_opening_library,
        "probe_ai_video",
        lambda path, ffmpeg_path: {
            "width": 720, "height": 1280, "duration": 4.0,
            "video_codec": "h264", "has_audio": False,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ai_opening_library, "frame_distance", lambda *args, **kwargs: 40,
        raising=False,
    )

    report = ai_opening_library.validate_ai_opening(
        reference, master, ffmpeg_path="ffmpeg"
    )

    assert report["passed"] is False
    assert "reference_frame_mismatch" in report["failures"]


def test_valid_vertical_silent_video_passes_ai_validation(tmp_path, monkeypatch):
    from app.services import ai_opening_library

    reference = tmp_path / "reference.jpg"
    master = tmp_path / "master.mp4"
    reference.write_bytes(b"image")
    master.write_bytes(b"video")
    monkeypatch.setattr(
        ai_opening_library,
        "probe_ai_video",
        lambda path, ffmpeg_path: {
            "width": 720, "height": 1280, "duration": 4.0,
            "video_codec": "h264", "has_audio": False,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ai_opening_library, "frame_distance", lambda *args, **kwargs: 12,
        raising=False,
    )

    report = ai_opening_library.validate_ai_opening(
        reference, master, ffmpeg_path="ffmpeg"
    )

    assert report["passed"] is True
    assert report["failures"] == []
