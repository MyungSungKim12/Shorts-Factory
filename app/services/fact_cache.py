"""검증된 사실 캐시 — 검색 할당량을 매 회차 소비하지 않으면서 사실 검증 규칙을 지키기 위한 저장소.

흐름:
- 그라운딩 검색 성공 → 검증된 topic(항목·수치·출처)을 여기 저장 (method=grounded_search)
- 그라운딩 할당량 소진 → 캐시에서 최근에 안 쓴 검증된 소재를 꺼내 재사용 (method=verified_cache)
- 캐시에도 쓸 게 없으면 → 해당 회차 중단 (model_memory 업로드는 규칙상 금지)

videos.sqlite의 verified_topics 테이블에 저장한다.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def _conn(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(data_dir / "videos.sqlite")
    db.execute("""
        CREATE TABLE IF NOT EXISTS verified_topics (
            topic TEXT PRIMARY KEY,
            slot INTEGER,
            payload TEXT NOT NULL,      -- topic.json 전체 (검증된 항목 포함)
            verified_at TEXT NOT NULL,
            last_used_at TEXT
        )
    """)
    return db


def save_verified(data_dir: Path, slot, topic_dict: dict) -> None:
    """그라운딩으로 검증된 소재를 캐시에 저장/갱신."""
    db = _conn(data_dir)
    try:
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO verified_topics (topic, slot, payload, verified_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(topic) DO UPDATE SET payload=excluded.payload, "
            "verified_at=excluded.verified_at, slot=excluded.slot",
            (topic_dict.get("topic", ""), slot,
             json.dumps(topic_dict, ensure_ascii=False), now, now),
        )
        db.commit()
    finally:
        db.close()


def pick_cached(data_dir: Path, slot, exclude_topics: list, reverify_days: int = 30) -> dict | None:
    """해당 회차 카테고리의 검증 캐시에서 재사용할 소재 1건 선택.

    - exclude_topics(최근 사용분)는 제외
    - 검증한 지 reverify_days 넘은 건 신선도 위험으로 제외 (불변 기록이라 길게 잡음)
    - last_used_at이 가장 오래된 것부터 재사용 (골고루 순환)
    """
    db = _conn(data_dir)
    try:
        cutoff = (datetime.now() - timedelta(days=reverify_days)).isoformat()
        rows = db.execute(
            "SELECT topic, payload FROM verified_topics "
            "WHERE slot = ? AND verified_at >= ? "
            "ORDER BY (last_used_at IS NULL) DESC, last_used_at ASC",
            (slot, cutoff),
        ).fetchall()
        exclude = set(exclude_topics or [])
        for topic, payload in rows:
            if topic in exclude:
                continue
            db.execute(
                "UPDATE verified_topics SET last_used_at = ? WHERE topic = ?",
                (datetime.now().isoformat(), topic),
            )
            db.commit()
            data = json.loads(payload)
            data["verification_method"] = "verified_cache"
            return data
        return None
    finally:
        db.close()


def cache_size(data_dir: Path, slot=None) -> int:
    """캐시에 쌓인 검증 소재 수 (회차 지정 시 해당 회차만)."""
    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return 0
    db = _conn(data_dir)
    try:
        if slot is None:
            return db.execute("SELECT COUNT(*) FROM verified_topics").fetchone()[0]
        return db.execute("SELECT COUNT(*) FROM verified_topics WHERE slot = ?", (slot,)).fetchone()[0]
    finally:
        db.close()
