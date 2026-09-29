"""재시작 가능한 AI 자산 선생성 배치의 계획과 안전장치."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class SubjectSeed:
    key: str
    title_ko: str
    exact_query: str
    category: str


@dataclass(frozen=True)
class AssetJob:
    job_id: str
    kind: str
    variant: int
    estimated_krw: float
    subject: SubjectSeed


def build_jobs(
    subjects: list[SubjectSeed],
    *,
    video_variants: int,
    image_estimate_krw: float,
    video_estimate_krw: float,
) -> list[AssetJob]:
    jobs: list[AssetJob] = []
    for subject in subjects:
        jobs.append(AssetJob(
            f"{subject.key}:image:1", "image", 1,
            float(image_estimate_krw), subject,
        ))
        for variant in range(1, max(0, int(video_variants)) + 1):
            jobs.append(AssetJob(
                f"{subject.key}:video:{variant}", "video", variant,
                float(video_estimate_krw), subject,
            ))
    return jobs


def load_subject_seeds(path: Path) -> list[SubjectSeed]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("subject seed file must contain a JSON list")
    seeds = []
    seen = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("subject seed must be an object")
        seed = SubjectSeed(
            key=str(item.get("key") or "").strip(),
            title_ko=str(item.get("title_ko") or "").strip(),
            exact_query=str(item.get("exact_query") or "").strip(),
            category=str(item.get("category") or "").strip(),
        )
        if not all(asdict(seed).values()):
            raise ValueError("subject seed fields cannot be empty")
        if seed.key in seen:
            raise ValueError(f"duplicate subject key: {seed.key}")
        seen.add(seed.key)
        seeds.append(seed)
    return seeds


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def load_or_create_state(
    path: Path,
    *,
    budget_krw: float,
    committed_start_krw: float,
) -> dict:
    destination = Path(path)
    if destination.is_file():
        return json.loads(destination.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "budget_krw": float(budget_krw),
        "committed_start_krw": float(committed_start_krw),
        "stop_reason": "",
        "jobs": {},
    }
    _write_json_atomic(destination, state)
    return state


def record_job_result(
    path: Path,
    state: dict,
    job: AssetJob,
    *,
    status: str,
    detail: dict,
) -> None:
    if status not in {"completed", "failed", "rejected"}:
        raise ValueError(f"unsupported batch job status: {status}")
    previous = state.setdefault("jobs", {}).get(job.job_id) or {}
    attempts = int(previous.get("attempts") or 0) + 1
    state["jobs"][job.job_id] = {
        "status": status,
        "attempts": attempts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "job": {
            "job_id": job.job_id,
            "kind": job.kind,
            "variant": job.variant,
            "estimated_krw": job.estimated_krw,
            "subject": asdict(job.subject),
        },
        "detail": dict(detail),
    }
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(Path(path), state)


def pending_jobs(jobs: list[AssetJob], state: dict) -> list[AssetJob]:
    results = state.get("jobs") or {}
    pending = []
    for job in jobs:
        result = results.get(job.job_id) or {}
        status = result.get("status")
        attempts = int(result.get("attempts") or 0)
        if status in {"completed", "rejected"}:
            continue
        if status == "failed" and attempts >= 3:
            continue
        pending.append(job)
    return pending


def stop_reason(
    *,
    batch_spent_krw: float,
    next_estimated_krw: float,
    budget_krw: float,
    free_bytes: int,
    min_free_bytes: int,
    credit_mode: str,
) -> str | None:
    if str(credit_mode) != "premium":
        return "credit_guard_free_mode"
    if int(free_bytes) < int(min_free_bytes):
        return "disk_reserve_limit"
    if float(batch_spent_krw) + float(next_estimated_krw) > float(budget_krw):
        return "batch_budget_limit"
    return None


def save_stop_reason(path: Path, state: dict, reason: str) -> None:
    state["stop_reason"] = str(reason)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(Path(path), state)


def run_batch(
    jobs: list[AssetJob],
    *,
    state_path: Path,
    state: dict,
    status_provider: Callable[[], dict],
    executor: Callable[[AssetJob], tuple[str, dict]],
    min_free_bytes: int,
) -> dict:
    """Execute each pending job once, persisting after every result."""
    state["stop_reason"] = ""
    for job in pending_jobs(jobs, state):
        current = status_provider()
        spent = max(
            0.0,
            float(current.get("committed_krw") or 0.0)
            - float(state.get("committed_start_krw") or 0.0),
        )
        reason = stop_reason(
            batch_spent_krw=spent,
            next_estimated_krw=job.estimated_krw,
            budget_krw=float(state.get("budget_krw") or 0.0),
            free_bytes=int(current.get("free_bytes") or 0),
            min_free_bytes=int(min_free_bytes),
            credit_mode=str(current.get("mode") or "free"),
        )
        if reason:
            save_stop_reason(state_path, state, reason)
            return state
        try:
            status, detail = executor(job)
        except Exception as exc:
            status, detail = "failed", {"error": " ".join(str(exc).split())[:1000]}
        record_job_result(
            state_path, state, job, status=status, detail=detail
        )
    save_stop_reason(state_path, state, "job_queue_exhausted")
    return state
