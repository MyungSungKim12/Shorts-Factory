"""Bounded reconciliation for interrupted manual prebuild cleanup."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.services.slot_reservations import ACTIVE_STATES, KST, fail_owned_slot


def _report_paths(data_dir: Path, run_id: str) -> list[Path]:
    root = Path(data_dir) / "recovery" / "manual-cleanup"
    return sorted(root.glob(f"{run_id}-*.json")) if root.is_dir() else []


def _safe_artifact_paths(
    data_dir: Path, run_id: str, report: dict
) -> tuple[Path | None, Path | None]:
    current_value = report.get("current_artifact_path")
    intended_value = report.get("intended_recovery_path")
    if current_value is None and intended_value is None:
        return None, None
    if not isinstance(current_value, str) or not isinstance(intended_value, str):
        raise ValueError("cleanup artifact paths are incomplete")
    current = Path(current_value)
    intended = Path(intended_value)
    expected_current = Path(data_dir) / "work" / run_id
    archive_root = (Path(data_dir) / "recovery" / "manual-artifacts").resolve()
    if current.resolve() != expected_current.resolve():
        raise ValueError("cleanup artifact source is outside the run target")
    if not intended.resolve().is_relative_to(archive_root):
        raise ValueError("cleanup artifact destination is outside recovery")
    return current, intended


def _reconcile_artifact(current: Path | None, intended: Path | None) -> bool:
    if current is None or intended is None:
        return True
    if current.exists():
        intended.parent.mkdir(parents=True, exist_ok=True)
        if intended.exists():
            return False
        current.replace(intended)
    return intended.is_dir()


def _reconcile_report(data_dir: Path, run_id: str, path: Path) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("run_id") != run_id:
            return False
        attempt = report.get("attempt")
        worker_id = report.get("worker_id")
        failed_stage = report.get("failed_stage")
        if not isinstance(attempt, int) or attempt < 1:
            return False
        if not isinstance(worker_id, str) or not worker_id.startswith(
            f"manual-prebuild:{run_id}:{attempt}:"
        ):
            return False
        if failed_stage not in ACTIVE_STATES:
            return False
        current, intended = _safe_artifact_paths(data_dir, run_id, report)
        if not _reconcile_artifact(current, intended):
            return False
        fail_owned_slot(
            data_dir,
            run_id,
            worker_id,
            failed_stage,
            datetime.now(tz=KST),
            artifact_path=str(intended) if intended is not None else None,
        )
        path.unlink()
        return True
    except Exception:
        return False


def reconcile_manual_cleanup(data_dir: Path, run_id: str) -> dict:
    """Retry safe artifact/state cleanup recorded by an earlier manual worker."""
    reports = _report_paths(Path(data_dir), run_id)
    complete = True
    for path in reports:
        if not _reconcile_report(Path(data_dir), run_id, path):
            complete = False
    return {"had_reports": bool(reports), "complete": complete}
