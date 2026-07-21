"""cron 전용 실행기 — 동일 회차를 잠그고 안전한 실패만 한 번 복구한다."""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv()

from app.services.recovery import run_with_recovery  # noqa: E402
from app.services.notifications import safe_error, send_alert  # noqa: E402
from app.services.temp_cleanup import cleanup_stale_temp_dirs  # noqa: E402
from scripts.run_daily import cleanup_old_work  # noqa: E402


def _notify(data_dir: Path, event_key: str, text: str) -> None:
    try:
        send_alert(data_dir, event_key, text=text)
    except Exception:
        return


def _scheduled_run_id(slot: int) -> str:
    return f"{datetime.now().astimezone():%Y%m%d}-{slot}"


def _recovery_details(data_dir: Path, run_id: str, fallback: Exception) -> tuple[str, str]:
    try:
        state = json.loads(
            (data_dir / "recovery" / f"{run_id}.json").read_text(encoding="utf-8")
        )
        stage = state.get("failed_stage")
        error = state.get("last_error")
        if isinstance(stage, str) and isinstance(error, str):
            return stage, safe_error(RuntimeError(error))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return "unknown", safe_error(fallback)


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

    run_id = _scheduled_run_id(slot)
    try:
        result = asyncio.run(run_with_recovery(
            data_dir, ffmpeg_path, slot, delay_seconds=delay_seconds
        ))
    except Exception as exc:
        stage, error = _recovery_details(data_dir, run_id, exc)
        event_kind = "timeout" if stage == "scheduler" and "시간 초과" in error else "exhausted"
        _notify(
            data_dir,
            f"recovery:{run_id}:{event_kind}",
            f"Recovery {event_kind}\nrun_id: {run_id}\nstage: {stage}\nerror: {error}",
        )
        print(f"\n===== 예약 파이프라인 복구 실패 =====\n{safe_error(exc)}")
        raise SystemExit(1) from exc

    if result.get("status") == "already_running":
        print(f"동일 회차가 이미 실행 중입니다: {result['run_id']}")
        return

    uploader = result.get("stages", {}).get("uploader", {})
    run_id = str(result.get("date", result.get("run_id", run_id)))
    if uploader.get("status") == "uploaded" and isinstance(uploader.get("url"), str):
        _notify(
            data_dir,
            f"upload:{run_id}:uploaded",
            f"Scheduled upload succeeded\nrun_id: {run_id}\nurl: {uploader['url']}",
        )
    else:
        reason = uploader.get("reason", uploader.get("status", "unknown"))
        _notify(
            data_dir,
            f"upload:{run_id}:skipped",
            f"Scheduled upload skipped\nrun_id: {run_id}\nreason: {safe_error(RuntimeError(str(reason)))}",
        )
    print("\n===== 예약 실행 결과 =====")
    print(f"성공 여부: {result.get('success')}")
    if uploader.get("status") == "uploaded":
        print(f"업로드: {uploader.get('url')}")
    else:
        print(f"업로드: {uploader.get('reason', uploader.get('status', '알 수 없음'))}")


if __name__ == "__main__":
    main()

