import json

from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)
TOKEN = {"X-Token": "secret"}


def test_longform_api_lists_jobs_and_serves_thumbnail(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "script.json").write_text(
        json.dumps({"title": "남극의 피폭포"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "workflow.json").write_text(
        json.dumps({"status": "DRAFT_TOPIC", "topic": {"title": "남극의 피폭포"}}),
        encoding="utf-8",
    )
    (run_dir / "thumbnail.png").write_bytes(b"png")

    response = client.get("/api/longform", headers=TOKEN)

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["run_id"] == "longform-demo"
    assert payload["jobs"][0]["thumbnail_url"] == "/api/longform/longform-demo/thumbnail"
    assert client.get("/api/longform/longform-demo/thumbnail", headers=TOKEN).status_code == 200


def test_longform_api_starts_preview_after_topic_approval(tmp_path, monkeypatch):
    from app.routes import longform

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "FFMPEG_PATH", "ffmpeg-test")
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    calls = []

    def fake_request(data_dir, run_id, stage, *, ffmpeg_path="ffmpeg", command_runner=None):
        calls.append((data_dir, run_id, stage, ffmpeg_path))
        return {"run_id": run_id, "status": "PREVIEW_REQUESTED"}

    monkeypatch.setattr(longform, "request_longform_stage", fake_request)

    response = client.post("/api/longform/longform-demo/approve-topic", headers=TOKEN)

    assert response.status_code == 200
    assert response.json()["status"] == "PREVIEW_REQUESTED"
    assert calls == [(tmp_path, "longform-demo", "preview", "ffmpeg-test")]
