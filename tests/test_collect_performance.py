import json
import sqlite3
from datetime import datetime, timezone

from scripts import collect_performance as collector


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def _seed_video(data_dir, video_id="v1", run_id="20260815-1", uploaded_at="2026-08-15T02:00:00+00:00"):
    db = sqlite3.connect(data_dir / "videos.sqlite")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT,
            topic TEXT, category TEXT, status TEXT NOT NULL, uploaded_at TEXT
        )
        """
    )
    db.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, 'uploaded', ?)",
        (video_id, run_id, "지하 도시", "사라진 지하 도시", "hidden_world", uploaded_at),
    )
    db.commit()
    db.close()


def _latest_snapshot(data_dir, video_id):
    db = sqlite3.connect(data_dir / "videos.sqlite")
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT * FROM video_performance_snapshots WHERE video_id=? ORDER BY snapshot_at DESC LIMIT 1",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _uploaded_row(data_dir, video_id):
    db = sqlite3.connect(data_dir / "videos.sqlite")
    try:
        return db.execute(
            "SELECT video_id, date, title, topic, category, status, uploaded_at FROM videos WHERE video_id=?",
            (video_id,),
        ).fetchone()
    finally:
        db.close()


def _owner_metrics(engaged_views=800):
    return {
        "engaged_views": engaged_views,
        "views": 1000,
        "engaged_view_rate": engaged_views / 1000,
        "estimated_minutes_watched": 600.0,
        "average_view_duration_sec": 45.0,
        "average_view_percentage": 75.0,
        "likes": 12,
        "comments": 1,
        "shares": 3,
        "subscribers_gained": 4,
        "subscribers_lost": 0,
        "analytics_end_date": "2026-08-17",
    }


def test_collector_persists_features_metrics_retention_and_report(tmp_path):
    _seed_video(tmp_path)

    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: {
            "v1": {"views": 1100, "likes": 12, "comments": 1}
        },
        owner_fetcher=lambda ids, start, end: {"v1": _owner_metrics()},
        retention_fetcher=lambda video_id, start, end: [
            {
                "elapsed_video_time_ratio": 0.5,
                "audience_watch_ratio": 0.72,
                "relative_retention_performance": 0.1,
            }
        ],
    )

    assert result["status"] == "success"
    assert result["videos_saved"] == 1
    assert result["retention_videos"] == 1
    snapshot = _latest_snapshot(tmp_path, "v1")
    assert snapshot["views"] == 1100
    assert snapshot["engaged_views"] == 800
    assert snapshot["source_status"] == "complete"
    report_path = tmp_path / "reports" / "performance_latest.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["collection"]["status"] == "success"
    assert report["summary"]["mature_videos"] == 1


def test_public_stats_are_saved_when_analytics_is_unavailable(tmp_path):
    _seed_video(tmp_path)

    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: {
            "v1": {"views": 900, "likes": 4, "comments": 0}
        },
        owner_fetcher=lambda *args: (_ for _ in ()).throw(RuntimeError("quota")),
        retention_fetcher=lambda *args: [],
    )

    assert result["status"] == "partial"
    assert result["errors"] == ["owner_metrics: quota"]
    assert _latest_snapshot(tmp_path, "v1")["views"] == 900
    assert _latest_snapshot(tmp_path, "v1")["source_status"] == "public_only"


def test_empty_retention_response_is_not_counted_as_a_saved_video(tmp_path):
    _seed_video(tmp_path)

    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: {
            "v1": {"views": 1100, "likes": 12, "comments": 1}
        },
        owner_fetcher=lambda ids, start, end: {"v1": _owner_metrics()},
        retention_fetcher=lambda video_id, start, end: [],
    )

    assert result["status"] == "success"
    assert result["retention_videos"] == 0


def test_failed_collection_does_not_mutate_upload_or_run_history(tmp_path):
    _seed_video(tmp_path)
    run_log = tmp_path / "logs" / "run-20260815-1.json"
    run_log.parent.mkdir()
    run_log.write_text('{"success":true}', encoding="utf-8")
    before_video = _uploaded_row(tmp_path, "v1")
    before_log = run_log.read_bytes()

    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: (_ for _ in ()).throw(RuntimeError("data api")),
        owner_fetcher=lambda *args: (_ for _ in ()).throw(
            RuntimeError("analytics api")
        ),
        retention_fetcher=lambda *args: [],
    )

    assert result["status"] == "failed"
    assert result["videos_saved"] == 0
    assert _uploaded_row(tmp_path, "v1") == before_video
    assert run_log.read_bytes() == before_log


def test_collector_handles_empty_upload_history_without_external_calls(tmp_path):
    calls = []

    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: calls.append("public"),
        owner_fetcher=lambda *args: calls.append("owner"),
        retention_fetcher=lambda *args: calls.append("retention"),
    )

    assert result["status"] == "success"
    assert result["videos_seen"] == 0
    assert calls == []
