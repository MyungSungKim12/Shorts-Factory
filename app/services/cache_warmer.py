"""Bounded, grounded-only warming for the verified topic cache."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.agents.researcher import GroundingUnavailable, _load_recent_topics, run_researcher
from app.services.fact_cache import cache_size, cache_stats, cached_topics


def warm_verified_cache(
    data_dir: Path,
    target_per_slot: int = 10,
    *,
    researcher: Callable[..., dict] | None = None,
    now: datetime | None = None,
) -> dict:
    """Fill cache slots 1--3 with one grounded attempt each when under target."""
    if target_per_slot < 1:
        raise ValueError("target_per_slot must be at least 1")

    data_dir = Path(data_dir)
    researcher = researcher or run_researcher
    now = now or datetime.now()
    recent_topics = set(_load_recent_topics(data_dir))
    result = {
        "target_per_slot": target_per_slot,
        "attempted_slots": [],
        "skipped_full_slots": [],
        "warmed_slots": [],
        "unavailable_slots": [],
        "quota_exhausted": False,
        "slot_sizes": {},
        "slot_stats": {},
    }

    for slot in (1, 2, 3):
        if cache_size(data_dir, slot, now=now) >= target_per_slot:
            result["skipped_full_slots"].append(slot)
            continue

        result["attempted_slots"].append(slot)
        exclusions = sorted(recent_topics | cached_topics(data_dir, now=now))
        run_id = f"cache-warm-{now:%Y%m%d}-{slot}"
        work_root = "cache-warm"
        try:
            researcher(
                data_dir,
                run_id=run_id,
                recent_topics=exclusions,
                content_format="ranking",
                verification_policy="grounded_only",
                work_root=work_root,
            )
            result["warmed_slots"].append(slot)
        except GroundingUnavailable as error:
            result["unavailable_slots"].append(slot)
            if error.daily_quota:
                result["quota_exhausted"] = True
                break
        finally:
            shutil.rmtree(data_dir / work_root / run_id, ignore_errors=True)

    work_root_path = data_dir / "cache-warm"
    try:
        work_root_path.rmdir()
    except OSError:
        pass
    result["slot_stats"] = {
        slot: cache_stats(data_dir, slot, now=now) for slot in (1, 2, 3)
    }
    result["slot_sizes"] = {
        slot: result["slot_stats"][slot]["active"] for slot in (1, 2, 3)
    }
    return result
