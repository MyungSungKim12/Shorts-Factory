import json

from app.agents import uploader


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
    assert captured["body"]["snippet"]["description"] == (
        "사하라의 눈 설명\n\n#사하라의눈 #리차트구조"
    )
