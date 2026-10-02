import sqlite3

from app.services.research_feedback import (
    build_research_feedback,
    topic_duplicate_reason,
)


def test_build_research_feedback_extracts_winning_patterns_and_avoid_subjects(tmp_path):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute(
        "CREATE TABLE videos (video_id TEXT, date TEXT, title TEXT, topic TEXT, status TEXT)"
    )
    db.execute(
        "CREATE TABLE video_features (video_id TEXT, category TEXT)"
    )
    db.execute(
        "CREATE TABLE video_performance_snapshots ("
        "video_id TEXT, snapshot_at TEXT, views INTEGER, likes INTEGER, "
        "shares INTEGER, subscribers_gained INTEGER, avg_view_percentage REAL)"
    )
    rows = [
        (
            "v1",
            "20260805-2",
            "유럽 지하, 2만 명 살던 도시가 버려진 미스터리",
            "유럽 지하에 숨겨진 고대 지하 도시의 비밀",
            "hidden_world",
            3409,
            41,
            4,
            5,
            102.9,
        ),
        (
            "v2",
            "20260820-4",
            "해저 화석이 말하는 오래된 바다",
            "고대 해양 화석의 형성 과정",
            "science_mystery",
            8,
            0,
            0,
            0,
            49.7,
        ),
    ]
    for row in rows:
        video_id, date, title, topic, category, views, likes, shares, subs, avp = row
        db.execute("INSERT INTO videos VALUES (?, ?, ?, ?, 'uploaded')", (video_id, date, title, topic))
        db.execute("INSERT INTO video_features VALUES (?, ?)", (video_id, category))
        db.execute(
            "INSERT INTO video_performance_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (video_id, "2026-08-26T00:00:00", views, likes, shares, subs, avp),
        )
    db.commit()
    db.close()

    feedback = build_research_feedback(tmp_path)

    assert feedback["winning_patterns"][0]["title"] == "유럽 지하, 2만 명 살던 도시가 버려진 미스터리"
    assert "지하" in feedback["winning_patterns"][0]["pattern_tags"]
    assert "숫자" in feedback["winning_patterns"][0]["pattern_tags"]
    assert feedback["longform_candidates"][0]["source_video_id"] == "v1"
    assert "롱폼" in feedback["longform_candidates"][0]["expansion_brief"]
    assert "유럽 지하에 숨겨진 고대 지하 도시의 비밀" in feedback["avoid_subjects"]
    assert any("지하" in bucket for bucket in feedback["evergreen_buckets"])


def test_topic_duplicate_reason_rejects_same_subject_even_when_title_changes():
    avoid_subjects = [
        "유럽 지하에 숨겨진 고대 문명이 건설한 거대 지하 도시의 비밀",
        "남극 빙하 아래 뜨거운 호수와 화산의 비밀",
    ]
    candidate = {
        "topic": "유럽 지하 도시, 수천 명이 숨어 살던 비밀",
        "core_question": "왜 이 지하 도시는 버려졌는가",
    }

    reason = topic_duplicate_reason(candidate, avoid_subjects)

    assert reason
    assert "기존 소재와 유사" in reason


def test_topic_duplicate_reason_allows_different_places_with_only_generic_words():
    avoid_subjects = [
        "산 속에 숨겨진 핵전쟁 최후의 방어선, 25톤 방폭문 뒤의 지하 도시"
    ]
    candidate = {
        "topic": "오르비에토 지하 도시의 1,200개 동굴",
        "hook_angle": "도시 아래 또 하나의 도시가 이어진다",
        "target_keyword": "Orvieto Underground",
        "core_question": "오르비에토 아래 공간은 왜 만들어졌을까?",
    }

    assert topic_duplicate_reason(candidate, avoid_subjects) is None


def test_topic_duplicate_reason_rejects_same_place_by_distinctive_facts():
    avoid_subjects = [
        "핀란드 지하 450m, 10만 년 핵폐기물 영원히 묻는 곳"
    ]
    candidate = {
        "topic": "온칼로 지하 저장소가 10만 년 동안 봉인되는 이유",
        "hook_angle": "핵폐기물을 사람보다 오래 보관하는 시설",
        "target_keyword": "Onkalo repository",
        "core_question": "왜 이 시설은 10만 년을 버텨야 할까?",
    }

    reason = topic_duplicate_reason(candidate, avoid_subjects)

    assert reason
    assert "기존 소재와 유사" in reason


def test_feedback_keeps_upload_history_with_current_average_view_column(tmp_path):
    db = sqlite3.connect(tmp_path / "videos.sqlite")
    db.execute(
        "CREATE TABLE videos (video_id TEXT, date TEXT, title TEXT, topic TEXT, status TEXT)"
    )
    db.execute(
        "CREATE TABLE video_performance_snapshots ("
        "video_id TEXT, snapshot_at TEXT, views INTEGER, likes INTEGER, "
        "shares INTEGER, subscribers_gained INTEGER, average_view_percentage REAL)"
    )
    db.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, 'uploaded')",
        ("v1", "20260924-4", "체르노빌 거대 레이더", "러시아 딱따구리 레이더",),
    )
    db.execute(
        "INSERT INTO video_performance_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("v1", "2026-09-25T00:00:00", 1200, 5, 1, 1, 72.5),
    )
    db.commit()
    db.close()

    feedback = build_research_feedback(tmp_path)

    assert "체르노빌 거대 레이더" in feedback["avoid_subjects"]
    assert feedback["winning_patterns"][0]["avg_view_percentage"] == 72.5
