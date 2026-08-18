"""영상 제작 특징과 YouTube 성과를 영구 보존하는 SQLite 저장소."""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _connect(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(data_dir) / "videos.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _init_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS video_features (
            video_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            uploaded_at TEXT,
            title TEXT,
            topic TEXT,
            category TEXT,
            hook_text TEXT,
            script_chars INTEGER,
            scene_count INTEGER,
            planned_duration_sec REAL,
            actual_duration_sec REAL,
            writer_mode TEXT,
            verification_method TEXT,
            ai_opening_used INTEGER,
            feature_source TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS video_performance_snapshots (
            video_id TEXT NOT NULL,
            snapshot_bucket TEXT NOT NULL,
            snapshot_at TEXT NOT NULL,
            age_hours REAL,
            views INTEGER,
            engaged_views INTEGER,
            engaged_view_rate REAL,
            estimated_minutes_watched REAL,
            average_view_duration_sec REAL,
            average_view_percentage REAL,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            subscribers_gained INTEGER,
            subscribers_lost INTEGER,
            analytics_end_date TEXT,
            source_status TEXT NOT NULL,
            PRIMARY KEY (video_id, snapshot_bucket)
        );

        CREATE INDEX IF NOT EXISTS idx_performance_snapshot_at
        ON video_performance_snapshots(snapshot_at);

        CREATE TABLE IF NOT EXISTS video_retention_points (
            video_id TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            elapsed_video_time_ratio REAL NOT NULL,
            audience_watch_ratio REAL,
            relative_retention_performance REAL,
            PRIMARY KEY (video_id, snapshot_date, elapsed_video_time_ratio)
        );
        """
    )


def init_performance_schema(data_dir: Path) -> None:
    with _connect(data_dir) as db:
        _init_schema(db)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _snapshot_bucket(value: Any) -> str:
    parsed = value if isinstance(value, datetime) else _parse_datetime(value)
    if parsed is None:
        raise ValueError("snapshot_at은 ISO 8601 날짜여야 합니다")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(
        hour=(parsed.hour // 6) * 6,
        minute=0,
        second=0,
        microsecond=0,
    ).isoformat()


def _json_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _hook_text(script: dict) -> str | None:
    hook = script.get("hook")
    if isinstance(hook, str):
        return hook.strip() or None
    if isinstance(hook, dict):
        for key in ("text", "narration", "hook"):
            value = hook.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    scenes = script.get("scenes")
    if isinstance(scenes, list) and scenes:
        first = scenes[0]
        if isinstance(first, dict) and isinstance(first.get("narration"), str):
            return first["narration"].strip() or None
    return None


def _ai_opening_used(produce: dict) -> int | None:
    intro = produce.get("intro")
    if not isinstance(intro, dict) or "ai_generation" not in intro:
        return None
    generation = intro.get("ai_generation")
    if isinstance(generation, bool):
        return int(generation)
    if not isinstance(generation, dict):
        return 0
    if "used" in generation:
        return int(bool(generation.get("used")))
    if "generated" in generation:
        return int(bool(generation.get("generated")))
    status = str(generation.get("status") or "").lower()
    if status:
        return int(status in {"ready", "success", "generated", "reused"})
    return int(bool(generation))


def list_uploaded_videos(data_dir: Path) -> list[dict]:
    db_file = Path(data_dir) / "videos.sqlite"
    if not db_file.exists():
        return []
    with _connect(data_dir) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
        ).fetchone()
        if table is None:
            return []
        columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
        required = {"video_id", "date", "status"}
        if not required.issubset(columns):
            return []
        selections = [
            name if name in columns else f"NULL AS {name}"
            for name in ("video_id", "date", "title", "topic", "category", "uploaded_at")
        ]
        rows = db.execute(
            f"SELECT {', '.join(selections)} FROM videos WHERE status='uploaded'"
        ).fetchall()
        return [dict(row) for row in rows]


def capture_video_features(
    data_dir: Path,
    *,
    captured_at: datetime | None = None,
) -> int:
    captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    videos = list_uploaded_videos(data_dir)
    if not videos:
        init_performance_schema(data_dir)
        return 0

    values = []
    for video in videos:
        run_id = str(video.get("date") or "")
        work_dir = Path(data_dir) / "work" / run_id
        topic_payload = _json_object(work_dir / "topic.json")
        script = _json_object(work_dir / "script.json")
        produce = _json_object(work_dir / "produce_log.json")
        scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
        narrations = [
            scene.get("narration", "")
            for scene in scenes
            if isinstance(scene, dict) and isinstance(scene.get("narration"), str)
        ]
        has_work = bool(topic_payload or script or produce)
        values.append(
            (
                video["video_id"],
                run_id,
                video.get("uploaded_at"),
                script.get("title") or video.get("title"),
                topic_payload.get("topic") or video.get("topic"),
                topic_payload.get("category") or video.get("category"),
                _hook_text(script),
                sum(len(text) for text in narrations) if scenes else None,
                len(scenes) if scenes else None,
                script.get("total_duration_sec") or produce.get("planned_duration"),
                produce.get("actual_duration") or produce.get("video_duration"),
                script.get("writer_mode"),
                topic_payload.get("verification_method"),
                _ai_opening_used(produce),
                "work_json" if has_work else "videos_table",
                captured.isoformat(),
            )
        )

    with _connect(data_dir) as db:
        _init_schema(db)
        db.executemany(
            """
            INSERT INTO video_features (
                video_id, run_id, uploaded_at, title, topic, category,
                hook_text, script_chars, scene_count, planned_duration_sec,
                actual_duration_sec, writer_mode, verification_method,
                ai_opening_used, feature_source, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                run_id=excluded.run_id,
                uploaded_at=COALESCE(video_features.uploaded_at, excluded.uploaded_at),
                title=COALESCE(video_features.title, excluded.title),
                topic=COALESCE(video_features.topic, excluded.topic),
                category=COALESCE(video_features.category, excluded.category),
                hook_text=COALESCE(video_features.hook_text, excluded.hook_text),
                script_chars=COALESCE(video_features.script_chars, excluded.script_chars),
                scene_count=COALESCE(video_features.scene_count, excluded.scene_count),
                planned_duration_sec=COALESCE(video_features.planned_duration_sec, excluded.planned_duration_sec),
                actual_duration_sec=COALESCE(video_features.actual_duration_sec, excluded.actual_duration_sec),
                writer_mode=COALESCE(video_features.writer_mode, excluded.writer_mode),
                verification_method=COALESCE(video_features.verification_method, excluded.verification_method),
                ai_opening_used=COALESCE(video_features.ai_opening_used, excluded.ai_opening_used),
                feature_source=CASE
                    WHEN video_features.feature_source='work_json' THEN video_features.feature_source
                    ELSE excluded.feature_source
                END,
                captured_at=excluded.captured_at
            """,
            values,
        )
    return len(values)


_SNAPSHOT_COLUMNS = (
    "video_id",
    "snapshot_bucket",
    "snapshot_at",
    "age_hours",
    "views",
    "engaged_views",
    "engaged_view_rate",
    "estimated_minutes_watched",
    "average_view_duration_sec",
    "average_view_percentage",
    "likes",
    "comments",
    "shares",
    "subscribers_gained",
    "subscribers_lost",
    "analytics_end_date",
    "source_status",
)


def save_performance_snapshots(data_dir: Path, rows: Iterable[dict]) -> int:
    prepared = []
    for row in rows:
        item = dict(row)
        item["snapshot_bucket"] = _snapshot_bucket(item.get("snapshot_at"))
        prepared.append(tuple(item.get(column) for column in _SNAPSHOT_COLUMNS))
    if not prepared:
        return 0
    update = ", ".join(
        f"{column}=excluded.{column}"
        for column in _SNAPSHOT_COLUMNS
        if column not in {"video_id", "snapshot_bucket"}
    )
    placeholders = ", ".join("?" for _ in _SNAPSHOT_COLUMNS)
    with _connect(data_dir) as db:
        _init_schema(db)
        db.executemany(
            f"""
            INSERT INTO video_performance_snapshots ({', '.join(_SNAPSHOT_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(video_id, snapshot_bucket) DO UPDATE SET {update}
            """,
            prepared,
        )
    return len(prepared)


def save_retention_points(
    data_dir: Path,
    video_id: str,
    snapshot_date: str,
    points: Iterable[dict],
) -> int:
    rows = [
        (
            video_id,
            snapshot_date,
            point.get("elapsed_video_time_ratio"),
            point.get("audience_watch_ratio"),
            point.get("relative_retention_performance"),
        )
        for point in points
        if point.get("elapsed_video_time_ratio") is not None
    ]
    if not rows:
        return 0
    with _connect(data_dir) as db:
        _init_schema(db)
        db.executemany(
            """
            INSERT INTO video_retention_points (
                video_id, snapshot_date, elapsed_video_time_ratio,
                audience_watch_ratio, relative_retention_performance
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id, snapshot_date, elapsed_video_time_ratio)
            DO UPDATE SET
                audience_watch_ratio=excluded.audience_watch_ratio,
                relative_retention_performance=excluded.relative_retention_performance
            """,
            rows,
        )
    return len(rows)


def retention_due_video_ids(
    data_dir: Path,
    now: datetime,
    *,
    limit: int = 20,
) -> list[str]:
    capture_video_features(data_dir, captured_at=now)
    today = now.astimezone(timezone.utc).date().isoformat()
    with _connect(data_dir) as db:
        _init_schema(db)
        candidates = db.execute(
            """
            SELECT f.video_id, f.uploaded_at
            FROM video_features AS f
            WHERE NOT EXISTS (
                SELECT 1 FROM video_retention_points AS r
                WHERE r.video_id=f.video_id AND r.snapshot_date=?
            )
            ORDER BY f.uploaded_at ASC
            """,
            (today,),
        ).fetchall()
    due = []
    current = now.astimezone(timezone.utc)
    for row in candidates:
        uploaded = _parse_datetime(row["uploaded_at"])
        if uploaded is None or (current - uploaded).total_seconds() < 48 * 3600:
            continue
        due.append(str(row["video_id"]))
        if len(due) >= max(0, limit):
            break
    return due


def _latest_performance_rows(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """
        SELECT p.*, f.run_id, f.title, f.topic, f.category, f.hook_text,
               f.actual_duration_sec, f.writer_mode, f.ai_opening_used
        FROM video_performance_snapshots AS p
        JOIN (
            SELECT video_id, MAX(snapshot_at) AS latest
            FROM video_performance_snapshots GROUP BY video_id
        ) AS newest
          ON newest.video_id=p.video_id AND newest.latest=p.snapshot_at
        LEFT JOIN video_features AS f ON f.video_id=p.video_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _group_summary(rows: list[dict], key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    result = []
    for label, members in grouped.items():
        views = [int(row["views"]) for row in members if row.get("views") is not None]
        result.append(
            {
                key: label,
                "videos": len(members),
                "median_views": round(statistics.median(views), 1) if views else None,
                "median_average_view_percentage": _median_optional(
                    row.get("average_view_percentage") for row in members
                ),
            }
        )
    return sorted(result, key=lambda item: (-item["videos"], item[key]))


def _median_optional(values: Iterable[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(statistics.median(numbers), 2) if numbers else None


def build_performance_report(data_dir: Path, generated_at: datetime) -> dict:
    with _connect(data_dir) as db:
        _init_schema(db)
        rows = _latest_performance_rows(db)
    mature = [row for row in rows if (row.get("age_hours") or 0) >= 24]
    views = [int(row["views"]) for row in mature if row.get("views") is not None]
    mature.sort(key=lambda row: int(row.get("views") or 0), reverse=True)
    warnings = []
    if len(mature) < 8:
        warnings.append("표본 8개 미만: 성과 차이를 소재 규칙으로 확정하지 마세요.")
    if any(row.get("source_status") != "complete" for row in mature):
        warnings.append("일부 영상은 공개 통계만 수집되어 시청 유지 지표가 비어 있습니다.")
    return {
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "summary": {
            "total_videos": len(rows),
            "mature_videos": len(mature),
            "median_views": round(statistics.median(views), 1) if views else None,
            "median_engaged_view_rate": _median_optional(
                row.get("engaged_view_rate") for row in mature
            ),
            "median_average_view_percentage": _median_optional(
                row.get("average_view_percentage") for row in mature
            ),
        },
        "groups": {
            "category": _group_summary(mature, "category"),
            "writer_mode": _group_summary(mature, "writer_mode"),
        },
        "videos": mature,
        "warnings": warnings,
    }
