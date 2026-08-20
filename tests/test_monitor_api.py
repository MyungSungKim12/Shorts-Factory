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


def test_auto_topics_combines_prepared_and_uploaded_runs_with_pagination(
    tmp_path, monkeypatch
):
    _create_video_db(tmp_path)
    prepared = tmp_path / "work" / "20260726-1"
    prepared.mkdir(parents=True)
    (prepared / "topic.json").write_text(
        json.dumps({"topic": "자동 생성 미스터리 소재"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (prepared / "script.json").write_text(
        json.dumps({"title": "자동 생성 영상 제목"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (prepared / "prepared.json").write_text(
        json.dumps({"prepared_at": "2026-07-26T09:15:00+09:00"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    response = client.get("/api/auto-topics?page=1&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["topics"][0] == {
        "run_id": "20260726-1",
        "slot": 1,
        "topic": "자동 생성 미스터리 소재",
        "title": "자동 생성 영상 제목",
        "status": "prepared",
        "generated_at": "2026-07-26T09:15:00+09:00",
        "uploaded_at": None,
    }
    assert payload["pagination"]["total_items"] == 6
    assert payload["pagination"]["has_next"] is True


def test_performance_summary_returns_dashboard_ready_snapshot(tmp_path, monkeypatch):
    report = {
        "generated_at": "2026-08-20T00:20:00+00:00",
        "summary": {
            "total_videos": 12,
            "mature_videos": 10,
            "median_views": 1063,
            "median_engaged_view_rate": 0.43,
            "median_average_view_percentage": 65.19,
        },
        "groups": {
            "category": [
                {
                    "category": "hidden_world",
                    "videos": 4,
                    "median_views": 1717.5,
                    "median_average_view_percentage": 76.66,
                },
                {
                    "category": "science_mystery",
                    "videos": 6,
                    "median_views": 1055,
                    "median_average_view_percentage": 66.22,
                },
            ],
            "duration_bucket": [],
            "title_pattern": [],
            "ai_opening": [],
        },
        "videos": [
            {
                "video_id": "top-video",
                "run_id": "20260805-2",
                "title": "유럽 지하, 2만 명 살던 도시",
                "topic": "유럽 지하 도시",
                "category": "hidden_world",
                "views": 3365,
                "engaged_view_rate": 0.57,
                "average_view_percentage": 103.4,
                "source_status": "complete",
            },
            {
                "video_id": "low-video",
                "run_id": "20260818-4",
                "title": "불타는 얼음",
                "topic": "메탄 하이드레이트",
                "category": "science_mystery",
                "views": 8,
                "engaged_view_rate": None,
                "average_view_percentage": None,
                "source_status": "public_only",
            },
        ],
        "warnings": ["일부 영상은 공개 통계만 수집되어 시청 유지 지표가 비어 있습니다."],
        "collection": {"status": "success", "videos_seen": 12, "errors": []},
    }
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "performance_latest.json").write_text(
        json.dumps(report, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    response = client.get("/api/performance-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["summary"]["median_views"] == 1063
    assert payload["top_categories"][0]["category"] == "hidden_world"
    assert payload["top_videos"][0]["url"] == "https://youtube.com/shorts/top-video"
    assert payload["watch_items"] == [
        {
            "run_id": "20260818-4",
            "title": "불타는 얼음",
            "views": 8,
            "reason": "low_views",
        }
    ]
    assert payload["collection"]["status"] == "success"


def test_performance_summary_missing_report_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    payload = client.get("/api/performance-summary").json()

    assert payload == {
        "available": False,
        "message": "성과 수집 리포트 없음",
        "summary": {},
        "top_categories": [],
        "top_videos": [],
        "watch_items": [],
        "warnings": [],
        "collection": {},
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/videos?page=0",
        "/api/videos?page_size=0",
        "/api/videos?page_size=51",
        "/api/history?page=0",
        "/api/history?page_size=0",
        "/api/history?page_size=51",
        "/api/auto-topics?page=0",
        "/api/auto-topics?page_size=51",
    ],
)
def test_paged_endpoints_reject_invalid_bounds(path):
    assert client.get(path).status_code == 422


def test_pipeline_run_keeps_fail_closed_dashboard_auth(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    assert client.post("/api/pipeline/run").status_code == 503

    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    assert client.post("/api/pipeline/run").status_code == 401
