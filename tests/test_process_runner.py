import subprocess

import pytest

from app.services import process_runner


def test_run_checked_converts_timeout_to_safe_runtime_error(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(process_runner.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match=r"ffmpeg.*5초.*시간 초과"):
        process_runner.run_checked(["ffmpeg", "-i", "input.mp4"], timeout=5)


def test_run_checked_returns_successful_process(monkeypatch):
    expected = subprocess.CompletedProcess(["ffprobe"], 0, stdout="ok", stderr="")
    monkeypatch.setattr(process_runner.subprocess, "run", lambda *args, **kwargs: expected)

    assert process_runner.run_checked(["ffprobe"], timeout=3, text=True) is expected

