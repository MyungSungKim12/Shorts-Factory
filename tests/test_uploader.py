import json

import pytest

from app.agents import uploader


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

    assert result.count("Camp Century.jpg") == 1
    assert "CRREL Researcher" in result
    assert "CC BY 2.0" in result
    assert "https://commons.wikimedia.org/wiki/File:Camp_Century.jpg" in result
    assert "pexels.com" not in result


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
