import hashlib
import json

import pytest

from app.agents import uploader
from app.services import quality_gate


def _package(tmp_path):
    script = {
        "format": "story",
        "title": "끝까지 읽어야 하는 이상한 지구 이야기!",
        "cta": "다음 기록도 궁금하다면 구독과 좋아요 부탁드립니다.",
        "scenes": [
            {"n": 1, "role": "hook", "narration": "첫 장면입니다."},
            {"n": 2, "role": "close", "narration": "처음의 질문은 이렇게 이어집니다."},
        ],
    }
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    produce = {
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "intro": {"text": "끝까지 읽어야 하는 이상한 지구 이야기"},
        "cta": {
            "text": script["cta"], "embedded_in_body": False,
            "audio_duration": 4.0,
        },
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "output.mp4").write_bytes(b"video")
    return script, produce


def _valid_probe():
    return {
        "width": 1080, "height": 1920, "duration": 66.0,
        "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        "black_ratio": 0.01, "audio_duration": 65.9,
        "duration_delta": 0.1, "internal_silence_max": 0.0,
    }


def test_complete_upload_package_passes_and_persists_report(tmp_path, monkeypatch):
    _package(tmp_path)
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    result = quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    assert result["passed"] is True
    saved = json.loads((tmp_path / "produce_log.json").read_text(encoding="utf-8"))
    assert saved["quality_gate"] == result


def test_quality_gate_rejects_missing_required_exact_source(tmp_path, monkeypatch):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": True,
        "exact_source_count": 0,
        "generic_fallback_count": 4,
        "unrelated_fallback_count": 0,
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    with pytest.raises(RuntimeError, match="visual_exact_source"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    saved = json.loads((tmp_path / "produce_log.json").read_text(encoding="utf-8"))
    assert "visual_exact_source" in saved["quality_gate"]["failures"]


def test_quality_gate_rejects_text_only_veo_as_exact_real_subject(
    tmp_path, monkeypatch
):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": True,
        "exact_source_count": 0,
        "generic_fallback_count": 4,
        "unrelated_fallback_count": 0,
        "opening_strategy": "vertex_veo",
    }
    produce["intro"]["ai_generation"] = {
        "provider": "vertex_veo",
        "model": "veo-3.1-fast-generate-001",
        "subject_aligned": True,
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    with pytest.raises(RuntimeError, match="visual_exact_source"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")


def test_quality_gate_rejects_ai_opening_without_reference_provenance(
    tmp_path, monkeypatch
):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": False,
        "exact_source_count": 0,
        "generic_fallback_count": 0,
        "unrelated_fallback_count": 0,
        "opening_strategy": "vertex_veo_image",
    }
    produce["intro"]["ai_generation"] = {
        "provider": "vertex_veo",
        "status": "ready",
        "asset_id": "asset-1",
        "subject_key": "richat-structure",
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    with pytest.raises(RuntimeError, match="ai_opening_provenance"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")


def test_quality_gate_accepts_logged_stock_fallback_when_exact_media_is_unavailable(
    tmp_path, monkeypatch
):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": True,
        "exact_source_count": 0,
        "generic_fallback_count": 4,
        "unrelated_fallback_count": 0,
        "opening_strategy": "stock_after_exact_failure",
    }
    produce["intro"]["ai_generation"] = {
        "provider": "vertex_veo",
        "status": "skipped_unverified_real_subject",
        "exact_media_error": "required exact Wikimedia media is unavailable",
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    result = quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    assert result["passed"] is True


def test_quality_gate_accepts_logged_stock_fallback_after_veo_failure(
    tmp_path, monkeypatch
):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": True,
        "exact_source_count": 0,
        "generic_fallback_count": 4,
        "unrelated_fallback_count": 0,
        "opening_strategy": "stock_after_veo_failure",
    }
    produce["intro"]["ai_generation"] = {
        "provider": "vertex_veo",
        "status": "failed",
        "error": "quota unavailable",
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    result = quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    assert result["passed"] is True


def test_quality_gate_rejects_unrelated_visual_fallback(tmp_path, monkeypatch):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": False,
        "exact_source_count": 0,
        "generic_fallback_count": 0,
        "unrelated_fallback_count": 1,
    }
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    with pytest.raises(RuntimeError, match="visual_unrelated_fallback"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")


def test_quality_gate_revalidates_claimed_exact_sources(tmp_path, monkeypatch):
    _, produce = _package(tmp_path)
    produce["visual_relevance"] = {
        "required_exact": True,
        "exact_source_count": 1,
        "generic_fallback_count": 0,
        "unrelated_fallback_count": 0,
    }
    produce["sources"] = [{
        "provider": "wikimedia_image",
        "media_id": "File:Flag of Mauritania.jpg",
        "keyword": "Richat Structure Mauritania",
        "exact_match": True,
    }]
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: _valid_probe())

    with pytest.raises(RuntimeError, match="visual_exact_source"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    saved = json.loads((tmp_path / "produce_log.json").read_text(encoding="utf-8"))
    assert "visual_exact_source" in saved["quality_gate"]["failures"]
    assert "visual_unrelated_fallback" in saved["quality_gate"]["failures"]


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        (lambda script, produce, report: produce.update(script_sha256="wrong"), "script_hash"),
        (lambda script, produce, report: produce["intro"].update(text="잘린 제목"), "intro_text"),
        (lambda script, produce, report: produce["cta"].update(embedded_in_body=True), "cta_duplicate"),
        (lambda script, produce, report: produce["cta"].update(text="다른 CTA"), "cta_text"),
        (lambda script, produce, report: report.update(duration_delta=0.8), "audio_duration_delta"),
        (lambda script, produce, report: report.update(internal_silence_max=1.5), "internal_silence"),
    ],
)
def test_upload_package_rejects_each_semantic_failure(
    tmp_path, monkeypatch, mutation, failure
):
    script, produce = _package(tmp_path)
    report = _valid_probe()
    mutation(script, produce, report)
    (tmp_path / "produce_log.json").write_text(
        json.dumps(produce, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(quality_gate, "probe_video", lambda *args: report)

    with pytest.raises(RuntimeError, match="업로드 품질검사 실패"):
        quality_gate.validate_upload_package(tmp_path, "ffmpeg")

    saved = json.loads((tmp_path / "produce_log.json").read_text(encoding="utf-8"))
    assert failure in saved["quality_gate"]["failures"]


def test_uploader_does_not_create_youtube_client_when_quality_gate_fails(
    tmp_path, monkeypatch
):
    work = tmp_path / "work" / "20260721-2"
    work.mkdir(parents=True)
    script, _ = _package(work)
    (work / "topic.json").write_text(json.dumps({
        "verification_method": "grounded_search"
    }), encoding="utf-8")
    called = []

    monkeypatch.setattr(uploader, "_validate_video_file", lambda path: None)
    monkeypatch.setattr(
        uploader, "validate_upload_package",
        lambda *args: (_ for _ in ()).throw(RuntimeError("QC blocked")),
        raising=False,
    )
    monkeypatch.setattr(uploader, "_get_youtube_client", lambda: called.append(True))

    with pytest.raises(RuntimeError, match="QC blocked"):
        uploader.run_uploader(tmp_path, "20260721-2")

    assert called == []

