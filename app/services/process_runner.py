"""외부 미디어 명령을 제한 시간 안에서 실행한다."""
from __future__ import annotations

import subprocess
from pathlib import Path


def run_checked(
    command: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    text: bool = False,
) -> subprocess.CompletedProcess:
    executable = Path(str(command[0])).name
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            cwd=cwd,
            timeout=timeout,
            text=text,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{executable} {timeout}초 시간 초과") from exc
    if result.returncode:
        stderr = result.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"{executable} 실패({result.returncode}):\n{stderr[-1200:]}")
    return result

