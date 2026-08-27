"""성과 기반 소재 피드백과 중복 회피 규칙."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable


EVERGREEN_BUCKETS = [
    "지하 도시·동굴·터널: 같은 지하라도 국가, 시대, 용도, 발견 계기를 바꿔 확장",
    "폐쇄·금지 시설: 벙커, 연구소, 군사 흔적, 출입 제한 구역처럼 실물 장면이 있는 장소",
    "고대 거석·잃어버린 도시: 돌, 수로, 성벽, 매몰 구조처럼 눈에 보이는 공학 미스터리",
    "사막·화산·호수·염호: 극한 지형과 숫자, 위험, 반전이 동시에 보이는 자연 기록",
    "빙하 아래 세계: 호수, 화산, 숲, 퇴적층처럼 얼음 아래 숨겨진 실제 대상",
    "위성으로 발견된 지형: 하늘에서 봐야 보이는 원형 구조, 충돌구, 고대 흔적",
    "인간 실험·기술이 남긴 지구 흔적: 자연 핵반응로, 광산, 초대형 장비, 실패한 도시",
]

_STOPWORDS = {
    "비밀", "미스터리", "이유", "진실", "기록", "세계", "소재", "구조",
    "동안", "아래", "속", "위", "없는", "있던", "있는", "말하는",
    "그리고", "하지만", "왜", "어떻게", "무엇", "정말", "실제",
}
_TOKEN_RE = re.compile(r"[0-9]+|[가-힣A-Za-z]{2,}")
_KOREAN_PARTICLES = (
    "으로", "에서", "에게", "까지", "부터", "처럼", "보다", "하고",
    "의", "에", "가", "이", "은", "는", "을", "를", "와", "과", "로",
)


def _normalize_token(token: str) -> str:
    for suffix in _KOREAN_PARTICLES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[:-len(suffix)]
    return token


def _connect_existing(data_dir: Path) -> sqlite3.Connection | None:
    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return None
    return sqlite3.connect(db_file)


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _tokens(text: object) -> set[str]:
    normalized = str(text or "").lower()
    return {
        _normalize_token(token)
        for token in _TOKEN_RE.findall(normalized)
        if _normalize_token(token) not in _STOPWORDS and len(_normalize_token(token)) >= 2
    }


def _dedupe(values: Iterable[object], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def _pattern_tags(title: str, topic: str) -> list[str]:
    text = f"{title} {topic}"
    checks = [
        ("지하", ("지하", "동굴", "터널", "벙커")),
        ("고대", ("고대", "문명", "거석", "유적", "도시")),
        ("금지·폐쇄", ("금지", "폐쇄", "제한", "버려진", "출입")),
        ("빙하", ("빙하", "남극", "북극", "얼음")),
        ("사막·화산·호수", ("사막", "화산", "호수", "염호", "분화구")),
        ("숫자", tuple(str(n) for n in range(10))),
        ("실물 구조", ("구조", "시설", "기지", "수로", "성벽", "광산")),
    ]
    tags = [label for label, keywords in checks if any(keyword in text for keyword in keywords)]
    return tags or ["장소", "반전", "실물 장면"]


def build_research_feedback(
    data_dir: Path,
    *,
    max_winners: int = 8,
    max_avoid: int = 120,
) -> dict:
    """Return compact feedback that helps the researcher exploit winners and avoid repeats."""
    fallback = {
        "winning_patterns": [],
        "avoid_subjects": [],
        "evergreen_buckets": EVERGREEN_BUCKETS,
    }
    db = _connect_existing(data_dir)
    if db is None:
        return fallback

    try:
        if not _table_exists(db, "videos"):
            return fallback
        video_columns = _columns(db, "videos")
        if not {"title", "status"}.issubset(video_columns):
            return fallback
        topic_column = "topic" if "topic" in video_columns else "NULL"

        avoid_rows = db.execute(
            f"SELECT title, {topic_column} FROM videos "
            "WHERE status = 'uploaded' ORDER BY date DESC"
        ).fetchall()
        avoid_subjects = _dedupe(
            (value for row in avoid_rows for value in row),
            max_avoid,
        )

        if not _table_exists(db, "video_performance_snapshots"):
            return {
                "winning_patterns": [],
                "avoid_subjects": avoid_subjects,
                "evergreen_buckets": EVERGREEN_BUCKETS,
            }

        features_join = ""
        category_expr = "''"
        if _table_exists(db, "video_features") and "category" in _columns(db, "video_features"):
            features_join = "LEFT JOIN video_features f ON f.video_id = v.video_id"
            category_expr = "COALESCE(f.category, '')"

        rows = db.execute(
            f"""
            WITH latest AS (
              SELECT s.*
              FROM video_performance_snapshots s
              JOIN (
                SELECT video_id, MAX(snapshot_at) AS snapshot_at
                FROM video_performance_snapshots
                GROUP BY video_id
              ) m ON m.video_id = s.video_id AND m.snapshot_at = s.snapshot_at
            )
            SELECT
              v.title,
              {topic_column},
              {category_expr} AS category,
              COALESCE(latest.views, 0) AS views,
              COALESCE(latest.likes, 0) AS likes,
              COALESCE(latest.shares, 0) AS shares,
              COALESCE(latest.subscribers_gained, 0) AS subscribers_gained,
              COALESCE(latest.avg_view_percentage, 0) AS avg_view_percentage
            FROM videos v
            JOIN latest ON latest.video_id = v.video_id
            {features_join}
            WHERE v.status = 'uploaded'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return fallback
    finally:
        db.close()

    winners = []
    for title, topic, category, views, likes, shares, subs, avp in rows:
        score = int(views or 0) + int(likes or 0) * 20 + int(shares or 0) * 80 + int(subs or 0) * 150
        if float(avp or 0) >= 80:
            score += 300
        winners.append({
            "title": str(title or ""),
            "topic": str(topic or ""),
            "category": str(category or ""),
            "views": int(views or 0),
            "likes": int(likes or 0),
            "shares": int(shares or 0),
            "subscribers_gained": int(subs or 0),
            "avg_view_percentage": round(float(avp or 0), 1),
            "pattern_tags": _pattern_tags(str(title or ""), str(topic or "")),
            "_score": score,
        })
    winners.sort(key=lambda item: item["_score"], reverse=True)
    for item in winners:
        item.pop("_score", None)

    return {
        "winning_patterns": winners[:max_winners],
        "avoid_subjects": avoid_subjects,
        "evergreen_buckets": EVERGREEN_BUCKETS,
    }


def topic_duplicate_reason(candidate: dict, avoid_subjects: Iterable[str]) -> str | None:
    """Return a reason when candidate appears to reuse the same subject as prior uploads."""
    candidate_text = " ".join(str(candidate.get(key, "") or "") for key in (
        "topic", "title", "hook_angle", "target_keyword", "core_question",
    ))
    candidate_tokens = _tokens(candidate_text)
    if len(candidate_tokens) < 3:
        return None
    candidate_compact = re.sub(r"\s+", "", candidate_text.lower())

    for subject in avoid_subjects or []:
        prior_text = str(subject or "")
        prior_tokens = _tokens(prior_text)
        if len(prior_tokens) < 3:
            continue
        prior_compact = re.sub(r"\s+", "", prior_text.lower())
        if len(prior_compact) >= 10 and (
            prior_compact in candidate_compact or candidate_compact in prior_compact
        ):
            return f"기존 소재와 유사: {prior_text}"
        overlap = candidate_tokens & prior_tokens
        smaller = min(len(candidate_tokens), len(prior_tokens))
        if smaller >= 3 and len(overlap) / smaller >= 0.5:
            return f"기존 소재와 유사: {prior_text}"
        if len(overlap) >= 2 and any(
            anchor in overlap
            for anchor in ("지하", "도시", "빙하", "호수", "화산", "사막", "동굴", "터널", "거석", "유럽", "남극")
        ):
            return f"기존 소재와 유사: {prior_text}"
    return None
