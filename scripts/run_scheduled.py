"""cron 전용 실행기 — 동일 회차를 잠그고 안전한 실패만 한 번 복구한다."""
import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv()

from app.services.recovery import run_with_recovery  # noqa: E402
from app.services.temp_cleanup import cleanup_stale_temp_dirs  # noqa: E402
from scripts.run_daily import cleanup_old_work  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("사용법: python scripts/run_scheduled.py <slot>")

    slot = int(sys.argv[1])
    if not 1 <= slot <= 6:
        raise SystemExit("slot은 1~6 사이여야 합니다")

    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
    delay_seconds = int(os.getenv("RECOVERY_DELAY_SECONDS", "900"))
    cleanup = cleanup_stale_temp_dirs()
    print(
        f"임시 작업 정리: {cleanup['removed_dirs']}개, "
        f"{cleanup['removed_bytes']}바이트"
    )
    cleanup_old_work(data_dir, int(os.getenv("WORK_RETENTION_DAYS", "7")))

    try:
        result = asyncio.run(run_with_recovery(
            data_dir, ffmpeg_path, slot, delay_seconds=delay_seconds
        ))
    except Exception as exc:
        print(f"\n===== 예약 파이프라인 복구 실패 =====\n{exc}")
        raise SystemExit(1) from exc

    if result.get("status") == "already_running":
        print(f"동일 회차가 이미 실행 중입니다: {result['run_id']}")
        return

    uploader = result.get("stages", {}).get("uploader", {})
    print("\n===== 예약 실행 결과 =====")
    print(f"성공 여부: {result.get('success')}")
    if uploader.get("status") == "uploaded":
        print(f"업로드: {uploader.get('url')}")
    else:
        print(f"업로드: {uploader.get('reason', uploader.get('status', '알 수 없음'))}")


if __name__ == "__main__":
    main()

