import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from app.services import performance_store as store


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def _seed_video(
    data_dir,
    video_id="v1",
    run_id="20260818-1",
    *,
    uploaded_at="2026-08-15T11:00:00+09:00",
    title="2만 명이 사라진 지하 도시",
    topic="지하 도시",
    category="hidden_world",
):
    db = sqlite3.connect(data_dir / "videos.sqlite")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            title TEXT,
            topic TEXT,
            category TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT
        )
        """
    )
    db.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, 'uploaded', ?)",
        (video_id, run_id, title, topic, category, uploaded_at),
    )
    db.commit()
    db.close()


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _rows(data_dir, query, params=()):
    db = sqlite3.connect(data_dir / "videos.sqlite")
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(query, params)]
    finally:
        db.close()


def _performance_row(video_id, at, views, **overrides):
    row = {
        "video_id": video_id,
        "snapshot_at": at,
        "age_hours": 72.0,
        "views": views,
        "engaged_views": 800,
        "engaged_view_rate": 0.8,
        "estimated_minutes_watched": 600.0,
        "average_view_duration_sec": 45.0,
        "average_view_percentage": 75.0,
        "likes": 10,
        "comments": 1,
        "shares": 2,
        "subscribers_gained": 3,
        "subscribers_lost": 0,
        "analytics_end_date": "2026-08-17",
        "source_status": "complete",
    }
    row.update(overrides)
    return row


def test_schema_upserts_one_snapshot_per_video_and_six_hour_bucket(tmp_path):
    _seed_video(tmp_path)
    store.init_performance_schema(tmp_path)

    store.save_performance_snapshots(
        tmp_path,
        [_performance_row("v1", "2026-08-18T06:10:00+00:00", 100)],
    )
    store.save_performance_snapshots(
        tmp_path,
        [_performance_row("v1", "2026-08-18T09:20:00+00:00", 120)],
    )

    rows = _rows(
        tmp_path,
        "SELECT video_id, snapshot_bucket, views FROM video_performance_snapshots",
    )
    assert rows == [
        {
            "video_id": "v1",
            "snapshot_bucket": "2026-08-18T06:00:00+00:00",
            "views": 120,
        }
    ]


def test_capture_features_survives_work_cleanup_and_only_enriches_known_values(tmp_path):
    _seed_video(tmp_path)
    work = tmp_path / "work" / "20260818-1"
    _write_json(
        work / "topic.json",
        {
            "topic": "지하 도시",
            "category": "hidden_world",
            "verification_method": "grounded_search",
        },
    )
    _write_json(
        work / "script.json",
        {
            "title": "2만 명이 사라진 지하 도시",
            "hook": "도시가 하루아침에 비었습니다.",
            "scenes": [
                {"n": 1, "narration": "첫 장면", "duration_sec": 7},
                {"n": 2, "narration": "두 번째 장면", "duration_sec": 8},
            ],
            "total_duration_sec": 60,
            "writer_mode": "llm",
        },
    )
    _write_json(
        work / "produce_log.json",
        {
            "actual_duration": 72.4,
            "intro": {"ai_generation": {"used": True}},
        },
    )

    assert store.capture_video_features(tmp_path, captured_at=NOW) == 1
    shutil.rmtree(work)
    assert store.capture_video_features(tmp_path, captured_at=NOW + timedelta(hours=1)) == 1

    row = _rows(tmp_path, "SELECT * FROM video_features WHERE video_id='v1'")[0]
    assert row["hook_text"] == "도시가 하루아침에 비었습니다."
    assert row["script_chars"] == len("첫 장면두 번째 장면")
    assert row["scene_count"] == 2
    assert row["planned_duration_sec"] == 60
    assert row["actual_duration_sec"] == 72.4
    assert row["writer_mode"] == "llm"
    assert row["verification_method"] == "grounded_search"
    assert row["ai_opening_used"] == 1
    assert row["feature_source"] == "work_json"


def test_retention_due_excludes_recent_and_already_collected_today(tmp_path):
    _seed_video(tmp_path, "old", "20260815-1")
    _seed_video(
        tmp_path,
        "recent",
        "20260818-1",
        uploaded_at="2026-08-18T10:00:00+00:00",
    )
    store.capture_video_features(tmp_path, captured_at=NOW)

    assert store.retention_due_video_ids(tmp_path, NOW, limit=20) == ["old"]

    store.save_retention_points(
        tmp_path,
        "old",
        "2026-08-18",
        [
            {
                "elapsed_video_time_ratio": 0.5,
                "audience_watch_ratio": 0.72,
                "relative_retention_performance": 0.1,
            }
        ],
    )
    store.save_retention_points(
        tmp_path,
        "old",
        "2026-08-18",
        [
            {
                "elapsed_video_time_ratio": 0.5,
                "audience_watch_ratio": 0.75,
                "relative_retention_performance": 0.2,
            }
        ],
    )

    assert store.retention_due_video_ids(tmp_path, NOW, limit=20) == []
    rows = _rows(tmp_path, "SELECT * FROM video_retention_points")
    assert len(rows) == 1
    assert rows[0]["audience_watch_ratio"] == 0.75


def test_report_uses_latest_mature_medians_and_warns_for_small_groups(tmp_path):
    for index, views in enumerate((1000, 1200, 3300), start=1):
        video_id = f"v{index}"
        _seed_video(
            tmp_path,
            video_id,
            f"2026081{index}-1",
            uploaded_at=f"2026-08-1{index}T11:00:00+09:00",
        )
    store.capture_video_features(tmp_path, captured_at=NOW)
    store.save_performance_snapshots(
        tmp_path,
        [
            _performance_row("v1", "2026-08-18T06:00:00+00:00", 900),
            _performance_row("v1", "2026-08-18T12:00:00+00:00", 1000),
            _performance_row("v2", "2026-08-18T12:00:00+00:00", 1200),
            _performance_row("v3", "2026-08-18T12:00:00+00:00", 3300),
        ],
    )

    report = store.build_performance_report(tmp_path, NOW)

    assert report["summary"]["median_views"] == 1200
    assert report["summary"]["mature_videos"] == 3
    assert report["warnings"] == [
        "표본 8개 미만: 성과 차이를 소재 규칙으로 확정하지 마세요."
    ]
    assert [row["views"] for row in report["videos"]] == [3300, 1200, 1000]
