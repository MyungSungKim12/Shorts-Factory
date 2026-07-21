from datetime import datetime

import pytest

from app.agents import researcher as researcher_agent
from app.services.fact_cache import cache_size, save_verified


def _topic(name: str, method: str = "grounded_search") -> dict:
    return {
        "topic": name,
        "ranking_size": 3,
        "items": [
            {"rank": rank, "name": f"item {rank}", "fact": f"fact {rank}", "source": "source"}
            for rank in (1, 2, 3)
        ],
        "verification_method": method,
    }


def _seed_verified(data_dir, slot: int, count: int) -> None:
    for index in range(count):
        save_verified(data_dir, slot, _topic(f"slot {slot} topic {index}"))


def test_full_slot_skips_grounded_call(tmp_path):
    from app.services.cache_warmer import warm_verified_cache

    for slot in (1, 2, 3):
        _seed_verified(tmp_path, slot, 10)

    calls = []
    result = warm_verified_cache(
        tmp_path,
        researcher=lambda *args, **kwargs: calls.append((args, kwargs)),
        now=datetime(2026, 7, 21, 6, 30),
    )

    assert result["skipped_full_slots"] == [1, 2, 3]
    assert result["attempted_slots"] == []
    assert result["slot_sizes"] == {1: 10, 2: 10, 3: 10}
    assert not calls


def test_quota_exhaustion_stops_remaining_slots(tmp_path):
    from app.agents.researcher import GroundingUnavailable
    from app.services.cache_warmer import warm_verified_cache

    calls = []

    def raise_daily_quota(*args, **kwargs):
        calls.append(kwargs)
        raise GroundingUnavailable("daily grounded quota exhausted", daily_quota=True)

    result = warm_verified_cache(
        tmp_path,
        researcher=raise_daily_quota,
        now=datetime(2026, 7, 21, 6, 30),
    )

    assert result["quota_exhausted"] is True
    assert result["attempted_slots"] == [1]
    assert result["slot_sizes"] == {1: 0, 2: 0, 3: 0}
    assert len(calls) == 1
    assert calls[0]["verification_policy"] == "grounded_only"
    assert calls[0]["run_id"] == "cache-warm-20260721-1"


def test_grounded_only_never_reads_cache_or_calls_model_memory(tmp_path, monkeypatch):
    from app.agents.researcher import GroundingUnavailable, run_researcher
    from app.services import fact_cache

    calls = []
    monkeypatch.setattr(
        researcher_agent,
        "call_agent",
        lambda **kwargs: calls.append(kwargs) or (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(
        fact_cache,
        "pick_cached",
        lambda *args, **kwargs: pytest.fail("grounded-only policy must not read verified cache"),
    )

    with pytest.raises(GroundingUnavailable) as exc_info:
        run_researcher(
            tmp_path,
            "cache-warm-20260721-1",
            recent_topics=[],
            verification_policy="grounded_only",
        )

    assert exc_info.value.daily_quota is False
    assert [call["grounded"] for call in calls] == [True]
    assert cache_size(tmp_path, 1) == 0


def test_verified_cache_rejects_non_grounded_payloads(tmp_path):
    with pytest.raises(ValueError, match="grounded_search"):
        save_verified(tmp_path, 1, _topic("not grounded", method="model_memory"))
