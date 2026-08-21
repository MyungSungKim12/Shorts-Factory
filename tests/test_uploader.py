import json
import subprocess
from datetime import datetime

import pytest

from app.agents import uploader


def test_upload_validation_accepts_short_video_without_content_minimum(
    tmp_path, monkeypatch
):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")
    probe = {
        "format": {"duration": "10.0"},
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920},
            {"codec_type": "audio"},
        ],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps(probe), stderr=""
        ),
    )

    uploader._validate_video_file(video)


def test_wikimedia_credits_are_unique_and_include_license_and_source():
    result = uploader._description_with_wikimedia_credits(
        "검증된 내용입니다.",
        [
            {
                "provider": "wikimedia_image",
                "media_id": "File:Camp Century.jpg",
                "attribution": "CRREL Researcher",
                "license": "CC BY 2.0",
                "source_url": "https://commons.wikimedia.org/wiki/File:Camp_Century.jpg",
            },
            {
                "provider": "wikimedia_image",
                "media_id": "File:Camp Century.jpg",
                "attribution": "CRREL Researcher",
                "license": "CC BY 2.0",
                "source_url": "https://commons.wikimedia.org/wiki/File:Camp_Century.jpg",
            },
            {"provider": "pexels_video", "source_url": "https://pexels.com/video/1"},
        ],
    )

    assert result.count("Camp Century") == 1
    assert "- File:Camp Century.jpg" not in result
    assert "- Camp Century.jpg" not in result
    assert "CRREL Researcher" in result
    assert "CC BY 2.0" in result
    assert "https://commons.wikimedia.org/wiki/File:Camp_Century.jpg" in result
    assert "pexels.com" not in result


def test_wikimedia_credit_hides_internal_image_filename():
    result = uploader._description_with_wikimedia_credits(
        "설명",
        [{
            "provider": "wikimedia_image",
            "media_id": "File:Nan Madol 11.png",
            "attribution": "Example Author",
            "license": "CC BY-SA 4.0",
            "source_url": "https://commons.wikimedia.org/wiki/File:Nan_Madol_11.png",
        }],
    )

    assert "Nan Madol 11" in result
    assert "- File:Nan Madol 11.png" not in result
    assert "- Nan Madol 11.png" not in result


def test_public_media_credits_include_nasa_sources_once():
    result = uploader._description_with_wikimedia_credits(
        "설명",
        [
            {
                "provider": "nasa_image",
                "media_id": "PIA00001",
                "attribution": "NASA",
                "license": "Public domain (NASA)",
                "source_url": "https://images.nasa.gov/details/PIA00001",
            },
            {
                "provider": "nasa_image",
                "media_id": "PIA00001",
                "attribution": "NASA",
                "license": "Public domain (NASA)",
                "source_url": "https://images.nasa.gov/details/PIA00001",
            },
        ],
    )

    assert result.count("https://images.nasa.gov/details/PIA00001") == 1
    assert "NASA" in result
    assert "Public domain (NASA)" in result
    assert "https://images.nasa.gov/details/PIA00001" in result


def test_synthetic_media_is_true_only_when_veo_footage_was_used():
    used = {
        "intro": {
            "ai_generation": {
                "provider": "vertex_veo",
                "status": "ready",
                "used_duration_sec": 3.0,
            }
        }
    }
    skipped = {
        "intro": {
            "ai_generation": {
                "provider": "vertex_veo",
                "status": "skipped_unverified_real_subject",
                "used_duration_sec": 0.0,
            }
        }
    }

    assert uploader._uses_synthetic_media(used) is True
    assert uploader._uses_synthetic_media(skipped) is False
    assert uploader._uses_synthetic_media({}) is False


def test_description_appends_clean_unique_topic_hashtags():
    result = uploader._description_with_hashtags(
        "리차트 구조를 살펴봅니다. #지구미스터리",
        ["사하라의 눈", "#리차트-구조", "지구미스터리", "사하라의 눈"],
    )

    assert result == (
        "리차트 구조를 살펴봅니다. #지구미스터리\n\n"
        "#사하라의눈 #리차트구조"
    )


def test_description_limits_hashtags_to_five():
    result = uploader._description_with_hashtags(
        "설명",
        ["하나", "둘", "셋", "넷", "다섯", "여섯"],
    )

    assert result.endswith("#하나 #둘 #셋 #넷 #다섯")
    assert "#여섯" not in result


def test_description_without_valid_tags_is_unchanged():
    assert uploader._description_with_hashtags(
        "원래 설명",
        ["#", "---", " "],
    ) == "원래 설명"


def test_description_does_not_exceed_length_limit():
    result = uploader._description_with_hashtags(
        "1234567890",
        ["추가태그"],
        max_length=10,
    )

    assert result == "1234567890"


def test_run_uploader_sends_hashtags_in_youtube_description(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    work_dir = data_dir / "work" / "20260727-1"
    work_dir.mkdir(parents=True)
    (work_dir / "output.mp4").write_bytes(b"video")
    (work_dir / "script.json").write_text(
        json.dumps(
            {
                "title": "사하라의 눈",
                "description": "사하라의 눈 설명",
                "tags": ["사하라의 눈", "리차트 구조"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work_dir / "topic.json").write_text(
        json.dumps(
            {
                "topic": "사하라의 눈",
                "category": "hidden_world",
                "verification_method": "grounded_search",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work_dir / "produce_log.json").write_text(
        json.dumps(
            {
                "intro": {
                    "ai_generation": {
                        "provider": "vertex_veo",
                        "status": "ready",
                        "used_duration_sec": 3.0,
                    }
                },
                "sources": [
                    {
                        "provider": "wikimedia_image",
                        "media_id": "File:Richat Structure.jpg",
                        "attribution": "NASA",
                        "license": "Public domain",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured = {}

    class FakeRequest:
        def next_chunk(self):
            return None, {"id": "video-123"}

    class FakeVideos:
        def insert(self, *, part, body, media_body):
            captured["part"] = part
            captured["body"] = body
            captured["media_body"] = media_body
            return FakeRequest()

    class FakeYoutube:
        def videos(self):
            return FakeVideos()

    monkeypatch.setattr(
        uploader,
        "validate_upload_package",
        lambda work_dir, ffmpeg_path: {"status": "passed"},
    )
    monkeypatch.setattr(uploader, "_get_youtube_client", lambda: FakeYoutube())
    monkeypatch.setattr(
        uploader,
        "MediaFileUpload",
        lambda path, mimetype, resumable: {
            "path": path,
            "mimetype": mimetype,
            "resumable": resumable,
        },
    )
    monkeypatch.setenv("DAILY_UPLOAD_LIMIT", "6")
    monkeypatch.setenv("UPLOAD_PRIVACY", "unlisted")

    result = uploader.run_uploader(data_dir, "20260727-1")

    assert result["status"] == "uploaded"
    assert captured["part"] == "snippet,status"
    description = captured["body"]["snippet"]["description"]
    assert description.startswith("사하라의 눈 설명\n\n#사하라의눈 #리차트구조")
    assert "자료 출처" in description
    assert "NASA" in description
    assert captured["body"]["status"]["containsSyntheticMedia"] is True
    db = uploader._init_db(data_dir)
    try:
        saved = db.execute(
            "SELECT category FROM videos WHERE video_id = ?",
            ("video-123",),
        ).fetchone()
    finally:
        db.close()
    assert saved == ("hidden_world",)


def test_init_db_adds_category_to_legacy_table(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    import sqlite3

    legacy = sqlite3.connect(data_dir / "videos.sqlite")
    legacy.execute(
        "CREATE TABLE videos (video_id TEXT PRIMARY KEY, date TEXT NOT NULL, "
        "title TEXT, topic TEXT, status TEXT NOT NULL, uploaded_at TEXT)"
    )
    legacy.close()

    db = uploader._init_db(data_dir)
    try:
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(videos)")
        }
    finally:
        db.close()

    assert "category" in columns


def test_run_uploader_rejects_prompt_instruction_title_before_youtube_call(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    work_dir = data_dir / "work" / "20260729-2"
    work_dir.mkdir(parents=True)
    (work_dir / "output.mp4").write_bytes(b"video")
    (work_dir / "script.json").write_text(
        json.dumps(
            {
                "title": "100자 이하 제목: 지하 수정 동굴의 비밀",
                "description": "설명",
                "tags": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work_dir / "topic.json").write_text(
        json.dumps(
            {
                "topic": "지하 수정 동굴의 비밀",
                "verification_method": "grounded_search",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        uploader,
        "_get_youtube_client",
        lambda: pytest.fail("잘못된 제목으로 YouTube 호출이 시작됨"),
    )

    with pytest.raises(ValueError, match="제목 지시문"):
        uploader.run_uploader(data_dir, "20260729-2")


def test_daily_upload_limit_cannot_be_configured_above_six(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    work_dir = data_dir / "work" / "20260810-1"
    work_dir.mkdir(parents=True)
    (work_dir / "output.mp4").write_bytes(b"video")
    (work_dir / "script.json").write_text(
        json.dumps({"title": "검증된 영상 제목", "description": "", "tags": []}),
        encoding="utf-8",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    db = uploader._init_db(data_dir)
    try:
        db.executemany(
            "INSERT INTO videos "
            "(video_id, date, title, topic, status, uploaded_at) "
            "VALUES (?, ?, '', '', 'uploaded', ?)",
            [(f"video-{n}", f"20260809-{n}", f"{today}T09:00:00") for n in range(6)],
        )
        db.commit()
    finally:
        db.close()
    monkeypatch.setenv("DAILY_UPLOAD_LIMIT", "99")
    monkeypatch.setattr(
        uploader,
        "_get_youtube_client",
        lambda: pytest.fail("일 6건 한도를 넘겨 YouTube API가 호출됨"),
    )

    result = uploader.run_uploader(data_dir, "20260810-1")

    assert result == {
        "status": "skipped",
        "reason": "일 업로드 한도(6건) 도달 — 내일 재시도",
    }
