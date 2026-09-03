"""Generate longform style previews or a reviewable longform MP4."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.agents.longform_producer import (
    generate_longform_style_previews,
    run_longform_producer,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="수동 검토용 롱폼 스타일 미리보기 또는 MP4를 생성합니다."
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ffmpeg-path", default=None)
    parser.add_argument("--preview-styles", action="store_true")
    parser.add_argument("--title", default="사막 아래 사라진 도시의 흔적")
    parser.add_argument("--chapter-title", default="첫 번째 단서")
    parser.add_argument(
        "--caption",
        default="위성사진 속 직선은 왜 사막 한가운데 남았을까요?",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    data_dir = args.data_dir or Path(os.getenv("DATA_DIR", "./data"))
    data_dir = Path(data_dir)
    if args.preview_styles:
        output_dir = data_dir / "longform" / "style-previews" / args.run_id
        manifest = generate_longform_style_previews(
            output_dir,
            title=args.title,
            chapter_title=args.chapter_title,
            caption=args.caption,
        )
        print(manifest["styles"][0]["preview_file"])
        return 0

    ffmpeg_path = args.ffmpeg_path or os.getenv("FFMPEG_PATH", "ffmpeg")
    result = run_longform_producer(data_dir, args.run_id, ffmpeg_path)
    print(result["output_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

