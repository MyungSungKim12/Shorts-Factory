"""Google Cloud 체험 크레딧을 보수적으로 추적하는 유료 호출 가드."""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


_LOCK = threading.RLock()


class PaidFeatureDisabled(RuntimeError):
    """잔액·만료·운영 모드 때문에 신규 유료 호출을 할 수 없음."""


@dataclass(frozen=True)
class CostReservation:
    data_dir: Path
    reservation_id: str
    feature: str
    estimated_usd: float
    estimated_krw: float
    run_id: str


def _billing_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "billing"


def _state_path(data_dir: Path) -> Path:
    return _billing_dir(data_dir) / "credit_state.json"


def _default_state() -> dict:
    return {"version": 1, "committed_krw": 0.0, "reservations": {}}


def _read_state(data_dir: Path) -> tuple[dict, bool]:
    path = _state_path(data_dir)
    if not path.exists():
        return _default_state(), True
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("committed_krw"), (int, float))
            or not isinstance(state.get("reservations"), dict)
        ):
            return _default_state(), False
        return state, True
    except (OSError, ValueError, json.JSONDecodeError):
        return _default_state(), False


def _write_state(data_dir: Path, state: dict) -> None:
    directory = _billing_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = _state_path(data_dir)
    temporary = directory / f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(destination)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _expired() -> bool:
    raw = os.getenv("CLOUD_CREDIT_EXPIRES_AT", "").strip()
    if not raw:
        return False
    try:
        expires = date.fromisoformat(raw)
    except ValueError:
        return True
    safety_days = max(0, int(_float_env("CLOUD_CREDIT_EXPIRY_SAFETY_DAYS", 3)))
    return date.today() >= expires - timedelta(days=safety_days)


def credit_status(data_dir: Path) -> dict:
    with _LOCK:
        state, valid = _read_state(data_dir)
        start = max(0.0, _float_env("CLOUD_CREDIT_START_KRW", 0.0))
        floor = max(0.0, _float_env("CLOUD_CREDIT_FLOOR_KRW", 80000.0))
        reserved = sum(
            float(item.get("estimated_krw", 0.0))
            for item in state["reservations"].values()
            if isinstance(item, dict)
        )
        committed = float(state["committed_krw"])
        remaining = max(0.0, start - committed - reserved)
        mode = os.getenv("AI_CREDIT_MODE", "auto").strip().lower()
        paid = (
            valid
            and mode in {"auto", "premium"}
            and not _expired()
            and remaining > floor
        )
        return {
            "mode": "premium" if paid else "free",
            "configured_mode": mode,
            "state_valid": valid,
            "start_krw": start,
            "floor_krw": floor,
            "committed_krw": round(committed, 4),
            "reserved_krw": round(reserved, 4),
            "expected_remaining_krw": round(remaining, 4),
            "expired_or_safety_window": _expired(),
            "reuse_existing_ai_assets": True,
        }


def paid_features_enabled(data_dir: Path) -> bool:
    return credit_status(data_dir)["mode"] == "premium"


def consume_mode_transition(data_dir: Path) -> tuple[str, str] | None:
    """모드가 실제로 바뀐 첫 호출에만 이전/현재 모드를 반환한다."""
    data_dir = Path(data_dir)
    with _LOCK:
        current = credit_status(data_dir)["mode"]
        state, valid = _read_state(data_dir)
        if not valid:
            return None
        previous = state.get("last_mode")
        state["last_mode"] = current
        _write_state(data_dir, state)
        if previous in {"premium", "free"} and previous != current:
            return previous, current
        return None


def reserve_cost(
    data_dir: Path,
    feature: str,
    estimated_usd: float,
    run_id: str,
) -> CostReservation:
    data_dir = Path(data_dir)
    usd = max(0.0, float(estimated_usd))
    rate = max(0.0, _float_env("CLOUD_USD_TO_KRW", 1400.0))
    krw = usd * rate
    with _LOCK:
        status = credit_status(data_dir)
        if not status["state_valid"]:
            raise PaidFeatureDisabled("크레딧 상태 파일 손상으로 무료 모드 전환")
        if status["mode"] != "premium":
            raise PaidFeatureDisabled(
                f"예상 잔액 하한 {status['floor_krw']:.0f}원 또는 무료 모드"
            )
        if status["expected_remaining_krw"] - krw <= status["floor_krw"]:
            raise PaidFeatureDisabled(
                f"호출 후 예상 잔액이 하한 {status['floor_krw']:.0f}원 이하"
            )
        state, _ = _read_state(data_dir)
        reservation_id = uuid.uuid4().hex
        state["reservations"][reservation_id] = {
            "feature": str(feature),
            "estimated_usd": usd,
            "estimated_krw": krw,
            "run_id": str(run_id),
            "reserved_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_state(data_dir, state)
    return CostReservation(data_dir, reservation_id, str(feature), usd, krw, str(run_id))


def commit_cost(
    reservation: CostReservation, actual_usd: float | None = None
) -> None:
    with _LOCK:
        state, valid = _read_state(reservation.data_dir)
        if not valid:
            raise PaidFeatureDisabled("크레딧 상태 파일 손상으로 비용 확정 중단")
        item = state["reservations"].pop(reservation.reservation_id, None)
        if item is None:
            return
        usd = reservation.estimated_usd if actual_usd is None else max(0.0, float(actual_usd))
        rate = max(0.0, _float_env("CLOUD_USD_TO_KRW", 1400.0))
        krw = usd * rate
        state["committed_krw"] = float(state["committed_krw"]) + krw
        _write_state(reservation.data_dir, state)
        ledger = _billing_dir(reservation.data_dir) / "ai_spend.jsonl"
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "committed",
            "feature": reservation.feature,
            "run_id": reservation.run_id,
            "cost_usd": round(usd, 8),
            "cost_krw": round(krw, 4),
        }
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def cancel_cost(reservation: CostReservation) -> None:
    with _LOCK:
        state, valid = _read_state(reservation.data_dir)
        if not valid:
            return
        if state["reservations"].pop(reservation.reservation_id, None) is not None:
            _write_state(reservation.data_dir, state)
