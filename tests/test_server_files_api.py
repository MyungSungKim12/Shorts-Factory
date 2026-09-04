import json
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)
TOKEN = {"X-Token": "secret"}


@pytest.fixture(autouse=True)
def configured_api(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    return tmp_path


def test_server_files_lists_only_safe_operational_files(configured_api):
    work = configured_api / "work" / "20260827-1"
    work.mkdir(parents=True)
    (work / "output.mp4").write_bytes(b"video")
    (work / "topic.json").write_text(json.dumps({"topic": "지하 도시"}), encoding="utf-8")
    (configured_api / ".env").write_text("TOKEN=leak", encoding="utf-8")
    credentials = configured_api / "credentials"
    credentials.mkdir()
    (credentials / "youtube.json").write_text("secret", encoding="utf-8")

    response = client.get("/api/server-files?page=1&page_size=20", headers=TOKEN)

    assert response.status_code == 200
    payload = response.json()
    paths = [item["relative_path"] for item in payload["files"]]
    assert "work/20260827-1/output.mp4" in paths
    assert "work/20260827-1/topic.json" in paths
    assert ".env" not in paths
    assert all("credentials" not in path for path in paths)
    assert payload["summary"]["total_files"] == 2


def test_server_files_can_filter_ai_cache_and_reports_disk_usage(configured_api):
    ai = configured_api / "media" / "ai_openings" / "asset-1"
    ai.mkdir(parents=True)
    (ai / "opening.mp4").write_bytes(b"ai-video")
    (configured_api / "logs").mkdir()
    (configured_api / "logs" / "run-20260827-1.json").write_text("{}", encoding="utf-8")

    response = client.get("/api/server-files?category=ai_cache", headers=TOKEN)

    assert response.status_code == 200
    payload = response.json()
    assert [item["category"] for item in payload["files"]] == ["ai_cache"]
    assert payload["summary"]["disk"]["total_bytes"] > 0
    assert payload["summary"]["categories"]["ai_cache"]["files"] == 1


def test_server_files_can_filter_longform_outputs(configured_api):
    longform = configured_api / "longform" / "longform-demo"
    longform.mkdir(parents=True)
    (longform / "output.mp4").write_bytes(b"longform-video")
    (longform / "preview_30s.mp4").write_bytes(b"longform-preview")
    (longform / "media_contact_sheet.png").write_bytes(b"png")
    (longform / "script.json").write_text(
        json.dumps({"format": "longform"}), encoding="utf-8"
    )
    (longform / "media_board.json").write_text(
        json.dumps({"workflow": "asset_first_longform"}), encoding="utf-8"
    )

    response = client.get("/api/server-files?category=longform", headers=TOKEN)

    assert response.status_code == 200
    payload = response.json()
    paths = [item["relative_path"] for item in payload["files"]]
    assert "longform/longform-demo/output.mp4" in paths
    assert "longform/longform-demo/preview_30s.mp4" in paths
    assert "longform/longform-demo/media_contact_sheet.png" in paths
    assert "longform/longform-demo/script.json" in paths
    assert "longform/longform-demo/media_board.json" in paths
    assert payload["summary"]["categories"]["longform"]["files"] == 5


def test_server_file_download_requires_token_and_rejects_forged_paths(configured_api):
    work = configured_api / "work" / "20260827-1"
    work.mkdir(parents=True)
    (work / "output.mp4").write_bytes(b"video")
    listing = client.get("/api/server-files", headers=TOKEN).json()
    file_id = listing["files"][0]["id"]

    assert client.get(f"/api/server-files/{file_id}/download").status_code == 401
    assert client.get(f"/api/server-files/{file_id}/download", headers=TOKEN).status_code == 200

    forged = "Li4vLmVudg"  # ../.env
    response = client.get(f"/api/server-files/{forged}/download", headers=TOKEN)
    assert response.status_code == 404


def test_server_files_skips_symlinked_files(configured_api):
    target = configured_api / "work" / "20260827-1" / "output.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"video")
    link = configured_api / "work" / "20260827-1" / "linked.mp4"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is not available")
        raise

    payload = client.get("/api/server-files", headers=TOKEN).json()

    assert "work/20260827-1/linked.mp4" not in [
        item["relative_path"] for item in payload["files"]
    ]
