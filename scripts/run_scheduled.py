"""cron 전용 실행기 — 동일 회차를 잠그고 안전한 실패만 한 번 복구한다."""
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

from dotenv import load_dotenv

load_dotenv()

from app.services.recovery import run_with_recovery  # noqa: E402
from app.services.notifications import send_alert  # noqa: E402
from app.services.temp_cleanup import cleanup_stale_temp_dirs  # noqa: E402
from scripts.run_daily import cleanup_old_work  # noqa: E402


def _notify(data_dir: Path, event_key: str, text: str) -> None:
    try:
        send_alert(data_dir, event_key, text=text)
    except Exception:
        return


def _scheduled_run_id(slot: int) -> str:
    return f"{datetime.now().astimezone():%Y%m%d}-{slot}"


_RECOVERY_STAGES = {"researcher", "writer", "producer", "uploader", "scheduler"}
_RUN_ID_PATTERN = re.compile(r"\d{8}-[1-6]")
_YOUTUBE_URL_PATTERN = re.compile(
    r"https://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]{3,}|https://youtu\.be/[A-Za-z0-9_-]{3,}"
)


def _recovery_details(data_dir: Path, run_id: str) -> tuple[str, str]:
    try:
        state = json.loads(
            (data_dir / "recovery" / f"{run_id}.json").read_text(encoding="utf-8")
        )
        if not isinstance(state, dict):
            return "unknown", "pipeline_failure"
        stage = state.get("failed_stage")
        error = state.get("last_error")
        stage = stage if stage in _RECOVERY_STAGES else "unknown"
        if stage == "scheduler" and isinstance(error, str) and "시간 초과" in error:
            return stage, "timeout"
        return stage, "pipeline_failure"
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return "unknown", "pipeline_failure"


def _skip_reason_category(reason: object) -> str:
    if reason == "오늘 영상 이미 업로드됨":
        return "already_uploaded"
    if isinstance(reason, str) and reason.startswith("일 업로드 한도("):
        return "daily_limit_reached"
    return "unknown"


def _safe_uploaded_url(value: object) -> str:
    if isinstance(value, str) and _YOUTUBE_URL_PATTERN.fullmatch(value):
        return value
    return "unavailable"


def _safe_run_id(value: object) -> str:
    return value if isinstance(value, str) and _RUN_ID_PATTERN.fullmatch(value) else "unknown"


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
        stage, error_category = _recovery_details(data_dir, run_id)
        event_kind = "timeout" if error_category == "timeout" else "exhausted"
        _notify(
            data_dir,
            f"recovery:{run_id}:{event_kind}",
            f"Recovery {event_kind}\nrun_id: {run_id}\nstage: {stage}"
            f"\nerror_category: {error_category}",
        )
        print("\n===== 예약 파이프라인 복구 실패 =====\nrecovery failure")
        raise SystemExit(1) from exc

    if result.get("status") == "already_running":
        print(f"동일 회차가 이미 실행 중입니다: {result['run_id']}")
        return

    uploader = result.get("stages", {}).get("uploader", {})
    run_id = _safe_run_id(result.get("date", result.get("run_id", run_id)))
    if uploader.get("status") == "uploaded":
        url = _safe_uploaded_url(uploader.get("url"))
        _notify(
            data_dir,
            f"upload:{run_id}:uploaded",
            f"Scheduled upload succeeded\nrun_id: {run_id}\nurl: {url}",
        )
    else:
        reason = _skip_reason_category(uploader.get("reason"))
        _notify(
            data_dir,
            f"upload:{run_id}:skipped",
            f"Scheduled upload skipped\nrun_id: {run_id}\nreason: {reason}",
        )
    print("\n===== 예약 실행 결과 =====")
    print(f"성공 여부: {result.get('success')}")
    if uploader.get("status") == "uploaded":
        print(f"업로드: {_safe_uploaded_url(uploader.get('url'))}")
    else:
        print(f"업로드: {_skip_reason_category(uploader.get('reason'))}")


if __name__ == "__main__":
    main()

