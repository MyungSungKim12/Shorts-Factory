import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


def test_longform_workflow_creates_reviewable_draft_from_performance_report(tmp_path, monkeypatch):
    from app.services import longform_workflow

    background = tmp_path / "background.jpg"
    Image.new("RGB", (1280, 720), (30, 40, 50)).save(background)
    monkeypatch.setattr(
        longform_workflow,
        "_find_thumbnail_background",
        lambda data_dir, script, candidate, revision=1, ffmpeg_path="ffmpeg": background,
    )

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "performance_latest.json").write_text(
        json.dumps(
            {
                "longform_candidates": [
                    {
                        "title": "남극 피폭포가 붉게 흐르는 진짜 이유",
                        "expansion_brief": "남극 피폭포를 출처, 지형, 미생물, 오해까지 TOP 구성으로 확장",
                        "views": 3300,
                        "pattern_tags": ["빙하", "실제 장소", "반전"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = longform_workflow.create_longform_draft(
        tmp_path,
        now=datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
    )

    run_dir = tmp_path / "longform" / result["run_id"]
    assert result["status"] == "DRAFT_TOPIC"
    assert result["topic"]["title"] != "남극 피폭포가 붉게 흐르는 진짜 이유"
    assert "TOP 5" in result["topic"]["title"]
    assert (run_dir / "script.json").is_file()
    assert (run_dir / "thumbnail.png").is_file()
    assert (run_dir / "thumbnail_background.jpg").is_file()
    script = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
    assert script["format"] == "longform"
    assert len(script["scenes"]) == 40
    assert script["thumbnail_main"]
    assert script["thumbnail_sub"]


def test_longform_workflow_records_stage_requests_without_uploading_early(tmp_path):
    from app.services.longform_workflow import request_longform_stage

    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    calls = []

    result = request_longform_stage(
        tmp_path,
        "longform-demo",
        "preview",
        command_runner=lambda command, cwd, log_file: calls.append(
            (command, cwd, log_file)
        ),
    )

    workflow = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert result["status"] == "PREVIEW_REQUESTED"
    assert workflow["status"] == "PREVIEW_REQUESTED"
    assert "upload_longform.py" not in " ".join(calls[0][0])


def test_longform_workflow_upload_stage_uses_reviewed_output_only(tmp_path):
    from app.services.longform_workflow import request_longform_stage

    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "output.mp4").write_bytes(b"mp4")
    calls = []

    result = request_longform_stage(
        tmp_path,
        "longform-demo",
        "upload",
        command_runner=lambda command, cwd, log_file: calls.append(
            (command, cwd, log_file)
        ),
    )

    assert result["status"] == "UPLOAD_REQUESTED"
    assert any("upload_longform.py" in part for part in calls[0][0])


def test_longform_workflow_can_regenerate_thumbnail_without_replacing_topic(tmp_path, monkeypatch):
    from app.services import longform_workflow

    background = tmp_path / "background.jpg"
    Image.new("RGB", (1280, 720), (30, 40, 50)).save(background)
    monkeypatch.setattr(
        longform_workflow,
        "_find_thumbnail_background",
        lambda data_dir, script, candidate, revision=1, ffmpeg_path="ffmpeg": background,
    )

    draft = longform_workflow.create_longform_draft(
        tmp_path,
        now=datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
    )

    result = longform_workflow.regenerate_longform_thumbnail(tmp_path, draft["run_id"])

    run_dir = tmp_path / "longform" / draft["run_id"]
    script = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
    assert result["status"] == "DRAFT_TOPIC"
    assert result["topic"]["title"] == draft["topic"]["title"]
    assert result["thumbnail_revision"] == 2
    assert script["thumbnail_main"] != "지구가 숨긴 TOP 5"
    assert (run_dir / "thumbnail.png").is_file()


def test_longform_workflow_refuses_card_thumbnail_when_background_is_missing(tmp_path, monkeypatch):
    from app.services import longform_workflow

    monkeypatch.setattr(
        longform_workflow,
        "_find_thumbnail_background",
        lambda data_dir, script, candidate, revision=1, ffmpeg_path="ffmpeg": None,
    )

    try:
        longform_workflow.create_longform_draft(tmp_path)
    except RuntimeError as exc:
        assert "썸네일 배경" in str(exc)
    else:
        raise AssertionError("draft creation must not fall back to a card thumbnail")


def test_longform_thumbnail_background_uses_landscape_candidate(tmp_path, monkeypatch):
    from app.services import longform_workflow
    from app.services.media_library import MediaCandidate

    portrait = MediaCandidate(
        provider="pexels_image",
        media_id="portrait",
        source_url="https://example.com/portrait",
        download_url="https://example.com/portrait.jpg",
        width=800,
        height=1200,
        media_type="image",
        keyword="underground city",
    )
    landscape = MediaCandidate(
        provider="pexels_image",
        media_id="landscape",
        source_url="https://example.com/landscape",
        download_url="https://example.com/landscape.jpg",
        width=1600,
        height=900,
        media_type="image",
        keyword="underground city",
    )
    selected = []

    monkeypatch.setattr(longform_workflow, "_wikimedia_image_candidates", lambda query: [])
    monkeypatch.setattr(longform_workflow, "_nasa_image_candidates", lambda query: [])
    monkeypatch.setattr(longform_workflow, "_pexels_landscape_photo_candidates", lambda query: [portrait, landscape])
    monkeypatch.setattr(longform_workflow, "_pexels_photo_candidates", lambda query: [])

    def fake_download(candidate, output):
        selected.append(candidate.media_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (candidate.width, candidate.height), (30, 40, 50))
        image.putpixel((0, 0), (200, 10, 10))
        image.save(output, quality=95)
        return output.stat().st_size

    monkeypatch.setattr(longform_workflow, "_download_candidate", fake_download)

    script = {
        "run_id": "longform-demo",
        "title": "땅속에 숨은 거대 세계 TOP 5",
    }
    result = longform_workflow._find_thumbnail_background(tmp_path, script, {})

    assert result is not None
    assert selected[0] == "landscape"
