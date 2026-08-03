from app.services.ai_storage_monitor import storage_status


def test_storage_status_warns_without_deleting_ai_assets(tmp_path, monkeypatch):
    asset = tmp_path / "media" / "ai_openings" / "asset-1" / "master.mp4"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"x" * 100)
    monkeypatch.setenv("AI_LIBRARY_WARN_BYTES", "50")
    monkeypatch.setenv("DISK_USAGE_WARN_PERCENT", "75")

    report = storage_status(
        tmp_path,
        disk_usage_fn=lambda path: (1000, 800, 200),
    )

    assert report["ai_library_bytes"] == 100
    assert report["disk_used_percent"] == 80.0
    assert report["warnings"] == ["disk_usage", "ai_library_size"]
    assert asset.exists()
