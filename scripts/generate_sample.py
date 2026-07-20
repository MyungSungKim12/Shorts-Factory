"""업로드 없이 격리된 스토리형 Shorts 샘플을 생성한다."""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

from app.agents.researcher import run_researcher  # noqa: E402
from app.agents.story_producer import run_story_producer  # noqa: E402
from app.agents.writer import run_writer  # noqa: E402
from app.services.media_probe import (  # noqa: E402
    ffprobe_path_for,
    probe_video,
    validate_sample,
)


_SAFE_SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


async def generate_sample(sample_id: str, data_dir: Path, ffmpeg_path: str) -> Path:
    """리서처→작가→프로듀서까지만 실행하고 MP4를 자동 검사한다."""
    if not _SAFE_SAMPLE_ID.fullmatch(sample_id):
        raise ValueError("sample_id는 영문·숫자로 시작하고 ._-만 포함해야 합니다")

    data_dir = Path(data_dir)
    sample_dir = data_dir / "samples" / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)

    run_researcher(
        data_dir,
        sample_id,
        recent_topics=[],
        content_format="story",
        work_root="samples",
        use_cache=False,
    )
    run_writer(data_dir, sample_id, content_format="story", work_root="samples")
    result = await run_story_producer(
        data_dir, sample_id, ffmpeg_path, work_root="samples"
    )
    output = Path(result["output_file"])
    report = probe_video(output, ffprobe_path_for(ffmpeg_path))
    failures = validate_sample(report)
    (sample_dir / "validation.json").write_text(
        json.dumps({"report": report, "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        raise RuntimeError(f"샘플 자동 검증 실패: {', '.join(failures)}")
    return output


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="업로드 없는 스토리형 Shorts 샘플 생성")
    parser.add_argument(
        "--sample-id",
        default=datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    args = parser.parse_args()
    os.environ["CONTENT_FORMAT"] = "story"
    os.environ["TTS_PROVIDER"] = "google"
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
    output = asyncio.run(generate_sample(args.sample_id, data_dir, ffmpeg_path))
    print(f"샘플 생성 완료: {output.resolve()}")


if __name__ == "__main__":
    main()
