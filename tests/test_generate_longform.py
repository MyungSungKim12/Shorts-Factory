import json
from pathlib import Path


def test_generate_longform_preview_command_creates_style_previews(tmp_path):
    from scripts.generate_longform import main

    exit_code = main([
        "--data-dir",
        str(tmp_path),
        "--run-id",
        "longform-demo",
        "--preview-styles",
        "--title",
        "사막 아래 사라진 도시의 흔적",
        "--chapter-title",
        "첫 번째 단서",
        "--caption",
        "위성사진 속 직선은 왜 사막 한가운데 남았을까요?",
    ])

    manifest = tmp_path / "longform" / "style-previews" / "longform-demo" / "manifest.json"
    assert exit_code == 0
    assert manifest.is_file()
    assert len(json.loads(manifest.read_text(encoding="utf-8"))["styles"]) == 3


def test_generate_longform_render_command_uses_producer(tmp_path, monkeypatch):
    import scripts.generate_longform as command

    seen = {}

    def fake_run(data_dir: Path, run_id: str, ffmpeg_path: str):
        seen.update(data_dir=data_dir, run_id=run_id, ffmpeg_path=ffmpeg_path)
        return {"output_file": str(data_dir / "longform" / run_id / "output.mp4")}

    monkeypatch.setattr(command, "run_longform_producer", fake_run)

    exit_code = command.main([
        "--data-dir",
        str(tmp_path),
        "--run-id",
        "longform-demo",
        "--ffmpeg-path",
        "ffmpeg-test",
    ])

    assert exit_code == 0
    assert seen == {
        "data_dir": tmp_path,
        "run_id": "longform-demo",
        "ffmpeg_path": "ffmpeg-test",
    }


def test_generate_longform_prepare_media_command_uses_preflight(tmp_path, monkeypatch):
    import scripts.generate_longform as command

    seen = {}

    def fake_prepare(data_dir: Path, run_id: str):
        seen.update(data_dir=data_dir, run_id=run_id)
        return {
            "run_id": run_id,
            "media_board_file": str(data_dir / "longform" / run_id / "media_board.json"),
        }

    monkeypatch.setattr(command, "prepare_longform_media_board", fake_prepare)

    exit_code = command.main([
        "--data-dir",
        str(tmp_path),
        "--run-id",
        "longform-demo",
        "--prepare-media",
    ])

    assert exit_code == 0
    assert seen == {
        "data_dir": tmp_path,
        "run_id": "longform-demo",
    }


def test_generate_longform_materialize_media_command_uses_downloader(tmp_path, monkeypatch):
    import scripts.generate_longform as command

    seen = {}

    def fake_materialize(data_dir: Path, run_id: str):
        seen.update(data_dir=data_dir, run_id=run_id)
        return {
            "run_id": run_id,
            "media_board_file": str(data_dir / "longform" / run_id / "media_board.json"),
        }

    monkeypatch.setattr(command, "materialize_longform_media_board", fake_materialize)

    exit_code = command.main([
        "--data-dir",
        str(tmp_path),
        "--run-id",
        "longform-demo",
        "--materialize-media",
    ])

    assert exit_code == 0
    assert seen == {
        "data_dir": tmp_path,
        "run_id": "longform-demo",
    }


def test_generate_longform_preview_30s_calls_preview_renderer(tmp_path, monkeypatch):
    import scripts.generate_longform as command

    seen = {}

    def fake_preview(data_dir: Path, run_id: str, ffmpeg_path: str):
        seen.update(data_dir=data_dir, run_id=run_id, ffmpeg_path=ffmpeg_path)
        return {"output_file": str(data_dir / "longform" / run_id / "preview_30s.mp4")}

    monkeypatch.setattr(command, "run_longform_preview", fake_preview)

    exit_code = command.main([
        "--data-dir",
        str(tmp_path),
        "--run-id",
        "longform-demo",
        "--ffmpeg-path",
        "ffmpeg-test",
        "--preview-30s",
    ])

    assert exit_code == 0
    assert seen == {
        "data_dir": tmp_path,
        "run_id": "longform-demo",
        "ffmpeg_path": "ffmpeg-test",
    }
