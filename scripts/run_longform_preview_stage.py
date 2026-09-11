"""Run longform media preparation and 2-minute preview as one server job."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="롱폼 미리보기 제작 단계를 순차 실행합니다.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    return parser


def _workflow_file(data_dir: Path, run_id: str) -> Path:
    return data_dir / "longform" / run_id / "workflow.json"


def _update_status(data_dir: Path, run_id: str, status: str, **extra) -> None:
    path = _workflow_file(data_dir, run_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state.update(
        {
            "run_id": run_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=str(ROOT), check=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir)
    env = os.environ.copy()
    env["DATA_DIR"] = str(data_dir)
    _update_status(data_dir, args.run_id, "PREVIEW_RUNNING")
    try:
        common = [
            sys.executable,
            "scripts/generate_longform.py",
            "--data-dir",
            str(data_dir),
            "--run-id",
            args.run_id,
            "--ffmpeg-path",
            args.ffmpeg_path,
        ]
        _run([*common, "--prepare-media"])
        _run([*common, "--materialize-media"])
        _run([*common, "--preview-30s"])
    except Exception as exc:
        _update_status(data_dir, args.run_id, "FAILED", failed_stage="preview", error=str(exc))
        raise
    _update_status(
        data_dir,
        args.run_id,
        "PREVIEW_READY",
        preview_url=f"/api/longform/{args.run_id}/preview",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
