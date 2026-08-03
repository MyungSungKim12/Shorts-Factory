"""영구 AI 자산과 서버 디스크 사용량을 삭제 없이 감시한다."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable


def _directory_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def storage_status(
    data_dir: Path,
    *,
    disk_usage_fn: Callable = shutil.disk_usage,
) -> dict:
    data_dir = Path(data_dir)
    total, used, free = disk_usage_fn(data_dir)
    used_percent = (float(used) / float(total) * 100.0) if total else 100.0
    library_bytes = _directory_bytes(data_dir / "media" / "ai_openings")
    disk_threshold = float(os.getenv("DISK_USAGE_WARN_PERCENT", "75"))
    library_threshold = int(os.getenv("AI_LIBRARY_WARN_BYTES", str(10 * 1024**3)))
    warnings = []
    if used_percent >= disk_threshold:
        warnings.append("disk_usage")
    if library_bytes >= library_threshold:
        warnings.append("ai_library_size")
    return {
        "disk_total_bytes": int(total),
        "disk_used_bytes": int(used),
        "disk_free_bytes": int(free),
        "disk_used_percent": round(used_percent, 2),
        "ai_library_bytes": library_bytes,
        "warnings": warnings,
        "auto_delete": False,
    }
