import json

import pytest


def _configure(monkeypatch, *, start="460418", floor="80000", rate="1400"):
    monkeypatch.setenv("AI_CREDIT_MODE", "auto")
    monkeypatch.setenv("CLOUD_CREDIT_START_KRW", start)
    monkeypatch.setenv("CLOUD_CREDIT_FLOOR_KRW", floor)
    monkeypatch.setenv("CLOUD_USD_TO_KRW", rate)
    monkeypatch.delenv("CLOUD_CREDIT_EXPIRES_AT", raising=False)


def test_paid_features_disable_before_reservation_crosses_eighty_thousand(tmp_path, monkeypatch):
    from app.services.credit_guard import PaidFeatureDisabled, reserve_cost

    _configure(monkeypatch, start="81000")

    with pytest.raises(PaidFeatureDisabled, match="80000"):
        reserve_cost(tmp_path, "veo", 1.0, "20260803-2")


def test_committed_cost_reduces_expected_balance_and_writes_ledger(tmp_path, monkeypatch):
    from app.services.credit_guard import commit_cost, credit_status, reserve_cost

    _configure(monkeypatch)
    reservation = reserve_cost(tmp_path, "veo_fast", 0.32, "20260803-2")
    commit_cost(reservation)

    status = credit_status(tmp_path)
    assert status["committed_krw"] == pytest.approx(448.0)
    assert status["expected_remaining_krw"] == pytest.approx(459970.0)
    events = [
        json.loads(line)
        for line in (tmp_path / "billing" / "ai_spend.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert events[-1]["feature"] == "veo_fast"
    assert events[-1]["run_id"] == "20260803-2"
    assert events[-1]["status"] == "committed"


def test_cancelled_reservation_does_not_reduce_balance(tmp_path, monkeypatch):
    from app.services.credit_guard import cancel_cost, credit_status, reserve_cost

    _configure(monkeypatch)
    reservation = reserve_cost(tmp_path, "veo_fast", 0.32, "20260803-2")
    cancel_cost(reservation)

    status = credit_status(tmp_path)
    assert status["committed_krw"] == 0
    assert status["reserved_krw"] == 0
    assert status["expected_remaining_krw"] == 460418


def test_corrupt_credit_state_fails_safe_to_free_mode(tmp_path, monkeypatch):
    from app.services.credit_guard import paid_features_enabled

    _configure(monkeypatch)
    billing = tmp_path / "billing"
    billing.mkdir()
    (billing / "credit_state.json").write_text("{broken", encoding="utf-8")

    assert paid_features_enabled(tmp_path) is False


def test_manual_free_mode_disables_paid_features_but_reports_assets_reusable(
    tmp_path, monkeypatch
):
    from app.services.credit_guard import credit_status, paid_features_enabled

    _configure(monkeypatch)
    monkeypatch.setenv("AI_CREDIT_MODE", "free")

    assert paid_features_enabled(tmp_path) is False
    assert credit_status(tmp_path)["reuse_existing_ai_assets"] is True
