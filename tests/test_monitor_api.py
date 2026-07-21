import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def _create_video_db(tmp_path):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute(
        """
        CREATE TABLE videos (
            video_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            title TEXT,
            topic TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT
        )
        """
    )
    for index in range(1, 6):
        db.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"video-{index}",
                f"2026072{index}",
                f"제목 {index}",
                f"주제 {index}",
                "uploaded",
                f"2026-07-2{index}T11:00:00",
            ),
        )
    db.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?)",
        ("old-video", "20260720-3", "교체된 영상", "주제", "replaced", "2026-07-26T11:00:00"),
    )
    db.commit()
    db.close()


def test_videos_returns_stable_uploaded_page_with_metadata(tmp_path, monkeypatch):
    _create_video_db(tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    response = client.get("/api/videos?page=2&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert [video["video_id"] for video in payload["videos"]] == ["video-3", "video-2"]
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total_items": 5,
        "total_pages": 3,
        "has_previous": True,
        "has_next": True,
    }


def test_videos_missing_db_returns_empty_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    payload = client.get("/api/videos").json()

    assert payload["videos"] == []
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 10,
        "total_items": 0,
        "total_pages": 0,
        "has_previous": False,
        "has_next": False,
    }


def test_history_sorts_valid_json_before_paginating(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    documents = [
        ("run-z.json", {"date": "run-old", "timestamp": "2026-07-20T11:00:00", "success": True}),
        ("run-a.json", {"date": "run-new", "timestamp": "2026-07-21T11:00:00", "success": True}),
        ("run-m.json", {"date": "run-middle", "timestamp": "2026-07-20T21:00:00", "success": False}),
    ]
    for filename, document in documents:
        (logs / filename).write_text(json.dumps(document), encoding="utf-8")
    (logs / "run-broken.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    response = client.get("/api/history?page=2&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert [run["date"] for run in payload["runs"]] == ["run-old"]
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total_items": 3,
        "total_pages": 2,
        "has_previous": True,
        "has_next": False,
    }


def test_history_merges_matching_recovery_without_changing_pagination(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    recovery = tmp_path / "recovery"
    logs.mkdir()
    recovery.mkdir()
    for slot in (1, 2):
        (logs / f"run-20260721-{slot}.json").write_text(json.dumps({
            "date": f"20260721-{slot}",
            "timestamp": f"2026-07-21T{10 + slot}:00:00",
            "success": slot == 2,
        }), encoding="utf-8")
    state = {
        "run_id": "20260721-1", "attempts": 2, "status": "exhausted",
        "failed_stage": "producer", "last_error": "audio failed",
        "next_retry_at": None, "updated_at": "2026-07-21T11:15:00",
    }
    (recovery / "20260721-1.json").write_text(json.dumps(state), encoding="utf-8")
    (recovery / "20260721-2.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    payload = client.get("/api/history?page=1&page_size=10").json()

    assert [run["date"] for run in payload["runs"]] == ["20260721-2", "20260721-1"]
    assert "recovery" not in payload["runs"][0]
    assert payload["runs"][1]["recovery"] == state
    assert payload["pagination"]["total_items"] == 2


@pytest.mark.parametrize(
    "path",
    [
        "/api/videos?page=0",
        "/api/videos?page_size=0",
        "/api/videos?page_size=51",
        "/api/history?page=0",
        "/api/history?page_size=0",
        "/api/history?page_size=51",
    ],
)
def test_paged_endpoints_reject_invalid_bounds(path):
    assert client.get(path).status_code == 422
