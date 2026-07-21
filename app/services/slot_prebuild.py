"""검증된 시험 영상을 다음 예약 회차의 작업 디렉터리로 승격한다."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
SCHEDULE = ((1, time(11, 0)), (2, time(17, 0)), (3, time(21, 0)))
REQUIRED_FILES = ("topic.json", "script.json", "produce_log.json", "output.mp4")


def next_scheduled_slot(now: datetime | None = None) -> tuple[str, datetime]:
    """현재 시각보다 엄격히 뒤에 있는 가장 가까운 KST 예약 회차를 반환한다."""
    current = now or datetime.now(tz=KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)

    for day_offset in (0, 1):
        day = current.date() + timedelta(days=day_offset)
        for slot, scheduled_time in SCHEDULE:
            scheduled_at = datetime.combine(day, scheduled_time, tzinfo=KST)
            if scheduled_at > current:
                return f"{day.strftime('%Y%m%d')}-{slot}", scheduled_at
    raise RuntimeError("다음 예약 회차를 계산하지 못했습니다")


def scheduled_run(now: datetime, slot: int) -> tuple[str, datetime]:
    """명시한 오늘의 예약 회차를 반환하고 이미 지난 회차는 거부한다."""
    current = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    schedule = dict(SCHEDULE)
    if slot not in schedule:
        raise RuntimeError(f"알 수 없는 예약 회차: {slot}")
    scheduled_at = datetime.combine(current.date(), schedule[slot], tzinfo=KST)
    if scheduled_at <= current:
        raise RuntimeError(f"이미 지난 예약 회차: {slot}")
    return f"{scheduled_at:%Y%m%d}-{slot}", scheduled_at


def _already_uploaded(data_dir: Path, run_id: str) -> bool:
    db_file = data_dir / "videos.sqlite"
    if not db_file.exists():
        return False
    try:
        with sqlite3.connect(db_file) as db:
            row = db.execute(
                "SELECT 1 FROM videos WHERE date = ? AND status = 'uploaded' LIMIT 1",
                (run_id,),
            ).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        raise RuntimeError(f"업로드 이력 확인 실패: {exc}") from exc


def ensure_target_available(data_dir: Path, run_id: str) -> None:
    """기존 예약 패키지나 업로드 이력이 있는 회차를 덮어쓰지 않는다."""
    data_dir = Path(data_dir)
    if _already_uploaded(data_dir, run_id):
        raise RuntimeError(f"이미 업로드된 회차입니다: {run_id}")
    if (data_dir / "work" / run_id).exists():
        raise RuntimeError(f"예약 회차 작업 디렉터리가 이미 존재합니다: {run_id}")


def _validate_staging(staging: Path, quality: dict) -> None:
    if not quality.get("passed") or quality.get("failures"):
        raise RuntimeError("품질검사를 통과하지 못한 영상은 예약할 수 없습니다")
    for name in REQUIRED_FILES:
        if not (staging / name).is_file():
            raise RuntimeError(f"사전 제작 필수 파일이 없습니다: {name}")

    script_bytes = (staging / "script.json").read_bytes()
    produce = json.loads((staging / "produce_log.json").read_text(encoding="utf-8"))
    if produce.get("script_sha256") != hashlib.sha256(script_bytes).hexdigest():
        raise RuntimeError("대본과 영상 해시가 일치하지 않습니다")


def promote_staging(
    data_dir: Path,
    staging_id: str,
    run_id: str,
    scheduled_at: datetime,
    quality: dict,
) -> Path:
    """staging 패키지를 완성된 다음 회차 work 디렉터리로 원자적으로 승격한다."""
    data_dir = Path(data_dir)
    staging = data_dir / "staging" / staging_id
    destination = data_dir / "work" / run_id
    temporary = destination.parent / f".{run_id}.promoting-{os.getpid()}"

    _validate_staging(staging, quality)
    ensure_target_available(data_dir, run_id)

    destination.parent.mkdir(parents=True, exist_ok=True)
    if temporary.exists():
        shutil.rmtree(temporary)
    try:
        shutil.copytree(staging, temporary)
        prepared = {
            "run_id": run_id,
            "staging_id": staging_id,
            "scheduled_at": scheduled_at.astimezone(KST).isoformat(),
            "prepared_at": datetime.now(tz=KST).isoformat(),
            "quality_gate": quality,
        }
        marker = temporary / "prepared.json"
        marker.write_text(
            json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    shutil.rmtree(staging)
    return destination
