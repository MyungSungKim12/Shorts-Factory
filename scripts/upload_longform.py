"""Upload a reviewed longform MP4 to YouTube."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.agents.longform_uploader import run_longform_uploader


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="검토 완료된 롱폼 MP4를 YouTube에 업로드합니다.")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    data_dir = args.data_dir or Path(os.getenv("DATA_DIR", "./data"))
    result = run_longform_uploader(Path(data_dir), args.run_id)
    if result.get("status") == "uploaded":
        print(result["url"])
    else:
        print(f"{result.get('status')}: {result.get('reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
