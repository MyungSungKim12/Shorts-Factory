"""Warm the grounded topic cache without producing or uploading content."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.services.cache_warmer import warm_verified_cache  # noqa: E402
from app.services.notifications import send_alert  # noqa: E402


def _notify(data_dir: Path, event_key: str, text: str) -> None:
    try:
        send_alert(data_dir, event_key, text=text)
    except Exception:
        return


def _alert_text(summary: dict) -> str:
    target = summary.get("target_per_slot")
    target = target if isinstance(target, int) and target > 0 else 0
    warmed = summary.get("warmed_slots")
    warmed = warmed if isinstance(warmed, list) else []
    sizes = summary.get("slot_sizes")
    sizes = sizes if isinstance(sizes, dict) else {}
    normalized_sizes = {
        slot: size if isinstance(size := sizes.get(slot, sizes.get(str(slot), 0)), int) else 0
        for slot in (1, 2, 3)
    }
    shortage = [slot for slot, size in normalized_sizes.items() if size < target]
    quota_exhausted = summary.get("quota_exhausted") is True
    return (
        "Cache warm completed"
        f"\nadded_slots: {len(warmed)}"
        f"\nsizes: {', '.join(f'{slot}={size}' for slot, size in normalized_sizes.items())}"
        f"\nquota_exhausted: {str(quota_exhausted).lower()}"
        f"\nshortage_slots: {', '.join(map(str, shortage)) or 'none'}"
    )


def main() -> None:
    os.chdir(ROOT)
    load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    summary = warm_verified_cache(data_dir)
    _notify(
        data_dir,
        f"cache-warm:{datetime.now():%Y%m%d}:summary",
        _alert_text(summary),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
