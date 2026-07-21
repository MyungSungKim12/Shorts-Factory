"""영상을 업로드하지 않고 제작·검증한 뒤 다음 예약 회차로 배치한다."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.agents.producer import run_producer  # noqa: E402
from app.agents.researcher import run_researcher  # noqa: E402
from app.agents.writer import run_writer  # noqa: E402
from app.content_format import get_content_format  # noqa: E402
from app.services.quality_gate import validate_upload_package  # noqa: E402
from app.services.recovery import acquire_global_lock, release_owned_lock  # noqa: E402
from app.services.slot_prebuild import (  # noqa: E402
    KST,
    next_scheduled_slot,
    promote_staging,
)


def _wait_for_lock(
    path: Path,
    owner_id: str,
    now_fn: Callable[[], datetime],
    wait_seconds: int,
    poll_seconds: int,
) -> None:
    waited = 0
    while not acquire_global_lock(path, owner_id, now_fn()):
        if waited >= wait_seconds:
            raise RuntimeError("사전 제작 전역 파이프라인 잠금 대기 시간 초과")
        duration = min(poll_seconds, wait_seconds - waited)
        time.sleep(duration)
        waited += duration


def prepare_next_slot(
    data_dir: Path,
    ffmpeg_path: str,
    *,
    now_fn: Callable[[], datetime] | None = None,
    use_lock: bool = True,
    lock_wait_seconds: int = 5400,
    lock_poll_seconds: int = 30,
) -> dict:
    """분리된 staging에서 완성한 패키지만 다음 미래 회차로 승격한다."""
    data_dir = Path(data_dir)
    now_fn = now_fn or (lambda: datetime.now(tz=KST))
    initial_run_id, _ = next_scheduled_slot(now_fn())
    initial_slot = initial_run_id.rsplit("-", 1)[1]
    staging_id = f"prebuild-{now_fn().strftime('%Y%m%d-%H%M%S')}-{initial_slot}"
    staging_dir = data_dir / "staging" / staging_id
    lock_path = data_dir / "recovery" / "pipeline.lock"
    lock_owner = f"staging:{staging_id}"
    lock_owned = False

    if use_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _wait_for_lock(
            lock_path,
            lock_owner,
            now_fn,
            lock_wait_seconds,
            lock_poll_seconds,
        )
        lock_owned = True

    try:
        selected = get_content_format()
        run_researcher(
            data_dir,
            staging_id,
            content_format=selected,
            work_root="staging",
        )
        run_writer(
            data_dir,
            staging_id,
            content_format=selected,
            work_root="staging",
        )
        asyncio.run(
            run_producer(
                data_dir,
                staging_id,
                ffmpeg_path,
                content_format=selected,
                work_root="staging",
            )
        )
        quality = validate_upload_package(staging_dir, ffmpeg_path)
        run_id, scheduled_at = next_scheduled_slot(now_fn())
        destination = promote_staging(
            data_dir, staging_id, run_id, scheduled_at, quality
        )
        return {
            "run_id": run_id,
            "scheduled_at": scheduled_at,
            "destination": destination,
            "quality_gate": quality,
        }
    finally:
        if lock_owned:
            release_owned_lock(lock_path, lock_owner, os.getpid())


def main() -> None:
    os.chdir(ROOT)
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="검증된 영상을 다음 11/17/21시 예약 회차에 사전 배치"
    )
    parser.add_argument("--lock-wait-seconds", type=int, default=5400)
    args = parser.parse_args()

    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
    result = prepare_next_slot(
        data_dir,
        ffmpeg_path,
        lock_wait_seconds=args.lock_wait_seconds,
    )
    print(f"사전 제작 완료: {result['destination'].resolve()}")
    print(f"예약 회차: {result['run_id']} ({result['scheduled_at'].isoformat()})")
    print("현재는 업로드하지 않았으며 해당 cron 회차가 업로드합니다.")


if __name__ == "__main__":
    main()
