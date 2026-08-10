"""Dashboard API for manual slot topic reservations and review actions."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from datetime import date, datetime
from pathlib import Path as FilePath
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query
from fastapi.responses import FileResponse
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.agents.orchestrator import run_pipeline
from app.services.manual_slot_actions import approve_slot, reject_slot, retry_slot, skip_slot
from app.services.manual_topic import ManualTopicInput, check_requested_topic
from app.services.slot_reservations import (
    KST,
    SlotConflict,
    append_slot_event,
    cancel_manual_reservation,
    create_check,
    events_after,
    init_slot_tables,
    list_slot_cards,
    reserve_checked_topic,
    save_check_result,
)


router = APIRouter(prefix="/api/slots", tags=["slots"])


def _valid_calendar_run_id(value: str) -> str:
    try:
        datetime.strptime(value[:8], "%Y%m%d")
    except ValueError as exc:
        raise ValueError("run_id contains an invalid calendar date") from exc
    return value


RunId = Annotated[
    str,
    Path(pattern=r"^\d{8}-[1-4]$"),
    AfterValidator(_valid_calendar_run_id),
]
_JSON_COLUMNS = {
    "include_constraints", "exclude_constraints", "reference_links",
    "request_json", "check_result",
}
_PUBLIC_SLOT_FIELDS = {
    "run_id", "slot", "mode", "original_input", "normalized_topic",
    "include_constraints", "exclude_constraints", "reference_links",
    "check_result", "state", "stage", "attempt", "worker_id",
    "production_at", "upload_at", "reserved_at", "locked_at", "approved_at",
    "rejected_at", "rejection_reason", "video_id", "created_at", "updated_at",
    "input_open", "upload_action",
}
_CHECK_RESULT_FIELDS = {
    "status", "reservable", "reason", "interpretations", "normalized_topic",
    "core_question", "channel_fit", "channel_warning", "verification_method",
    "safety", "sources", "visual", "topic_payload", "grounding_error",
}
_PUBLIC_GROUNDING_ERRORS = {
    "verification_method",
    "verified_at",
    "distinct_sources",
    "fact_source_linkage",
    "topic_contract",
}
_SENSITIVE_KEYS = {
    "api_key", "authorization", "cookie", "credential", "credentials",
    "password", "provider_payload", "provider_response", "raw_payload",
    "raw_response", "secret", "session", "token",
}
_EVENT_LEVELS = {"debug", "info", "warning", "error"}
_EVENT_METADATA_CODES = {
    "status", "failed_stage", "reason", "grounding_error", "visual_level",
    "verification_method", "mode",
}
_EVENT_METADATA_COUNTS = {
    "attempt": 1_000,
    "interpretation_count": 5,
    "visual_candidate_count": 1_000_000,
}
_EVENT_METADATA_FLAGS = {"channel_warning", "truncated"}
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVENT_SECRET_VALUE = re.compile(
    r'''(?ix)
    (?P<label>
        (?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|credential|
        secret|password|cookie|session|raw[_-]?(?:response|
        payload|body|headers|request)|provider[_-]?(?:response|payload|body|headers))
        \s*[:=]\s*
    )
    (?P<value>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^\s,;}\]]+)
    ''',
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:basic|bearer)\s+[^\s,;}\]]+"
)
_BOT_TOKEN = re.compile(r"(?<!\d)\d{5,15}:[A-Za-z0-9_-]{20,}")


class TopicCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic_input: str = Field(min_length=1, max_length=300)
    emphasis: str = Field(default="", max_length=500)
    include: str = Field(default="", max_length=500)
    exclude: str = Field(default="", max_length=500)
    reference_links: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("reference_links")
    @classmethod
    def _https_references_only(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            parsed = urlparse(value.strip())
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("reference links must use HTTPS")
            cleaned.append(value.strip())
        return cleaned

    def to_domain(self) -> ManualTopicInput:
        return ManualTopicInput(
            topic_input=self.topic_input,
            emphasis=self.emphasis,
            include_text=self.include,
            exclude_text=self.exclude,
            reference_urls=tuple(self.reference_links),
        )


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checked: Literal[True]


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=300)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["same_topic", "new_topic"]


def require_dashboard_token(x_token: str = Header(default="")) -> None:
    """Fail closed and authenticate dashboard mutations and artifact reads."""
    token = os.getenv("DASHBOARD_TOKEN", "")
    if not token:
        raise HTTPException(
            status_code=503,
            detail="DASHBOARD_TOKEN 미설정으로 원격 실행이 차단되어 있습니다",
        )
    if x_token != token:
        raise HTTPException(status_code=401, detail="관리자 토큰이 필요합니다")


def _data_dir() -> FilePath:
    # Lazy import avoids a router/main import cycle and keeps main.DATA_DIR as
    # the single runtime/test configuration point.
    from app import main

    return FilePath(main.DATA_DIR)


def _ffmpeg_path() -> str:
    from app import main

    return main.FFMPEG_PATH


def _now() -> datetime:
    return datetime.now(tz=KST)


def _decode_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    for column in _JSON_COLUMNS:
        value = result.get(column)
        result[column] = json.loads(value) if value else None
    return result


def _manual_slot(data_dir: FilePath, run_id: str) -> dict | None:
    init_slot_tables(data_dir)
    with sqlite3.connect(data_dir / "videos.sqlite") as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM slot_reservations WHERE run_id = ? AND mode = 'manual'",
            (run_id,),
        ).fetchone()
    return _decode_row(row) if row is not None else None


def _manual_slot_or_404(data_dir: FilePath, run_id: str) -> dict:
    slot = _manual_slot(data_dir, run_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="회차 예약을 찾을 수 없습니다")
    return slot


def _sanitize(value):
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).casefold() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _redact_event_message(value: object) -> str:
    text = str(value) if isinstance(value, str) else ""
    text = _AUTHORIZATION_VALUE.sub("Authorization: [redacted]", text)
    text = _EVENT_SECRET_VALUE.sub(r"\g<label>[redacted]", text)
    return _BOT_TOKEN.sub("[redacted]", text)[:500]


def _public_event_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _EVENT_METADATA_CODES:
        item = value.get(key)
        if isinstance(item, str) and _SAFE_CODE.fullmatch(item):
            result[key] = item
    for key, maximum in _EVENT_METADATA_COUNTS.items():
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= maximum:
            result[key] = item
    for key in _EVENT_METADATA_FLAGS:
        item = value.get(key)
        if isinstance(item, bool):
            result[key] = item
    return result


def _public_event(value: object, run_id: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    event_id = value.get("id")
    if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
        return None
    stage = value.get("stage")
    if not isinstance(stage, str) or not _SAFE_CODE.fullmatch(stage):
        stage = "unknown"
    level = value.get("level")
    if level not in _EVENT_LEVELS:
        level = "info"
    created_at = value.get("created_at")
    if isinstance(created_at, str) and len(created_at) <= 64:
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
        else:
            if parsed.tzinfo is None:
                created_at = None
    else:
        created_at = None
    return {
        "id": event_id,
        "run_id": run_id,
        "stage": stage,
        "level": level,
        "message": _redact_event_message(value.get("message")),
        "metadata": _public_event_metadata(value.get("metadata")),
        "created_at": created_at,
    }


def _public_slot(slot: dict) -> dict:
    result = {
        key: _sanitize(value)
        for key, value in slot.items()
        if key in _PUBLIC_SLOT_FIELDS
    }
    check_result = result.get("check_result")
    if isinstance(check_result, dict):
        result["check_result"] = {
            key: _sanitize(value)
            for key, value in check_result.items()
            if key in _CHECK_RESULT_FIELDS
        }
        grounding_error = result["check_result"].get("grounding_error")
        if (
            not isinstance(grounding_error, str)
            or grounding_error not in _PUBLIC_GROUNDING_ERRORS
        ):
            result["check_result"].pop("grounding_error", None)
    run_id = result.get("run_id")
    if "slot" not in result and isinstance(run_id, str):
        result["slot"] = int(run_id[-1])
    return result


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError) or str(exc) == "입력 시간이 종료되었습니다":
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _call_service(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (SlotConflict, ValueError) as exc:
        raise _service_error(exc) from exc


def _run_topic_check_job(data_dir: FilePath, run_id: str, request: ManualTopicInput) -> None:
    try:
        result = check_requested_topic(data_dir, run_id, request)
    except Exception:
        result = {
            "status": "failed", "reservable": False,
            "reason": "topic_check_failed",
        }
        try:
            append_slot_event(
                data_dir, run_id, "topic_check", "error",
                "소재 사전 검사 중 오류가 발생했습니다",
            )
        except Exception:
            pass
    try:
        save_check_result(data_dir, run_id, result, _now())
    except SlotConflict:
        # A state action that won the race owns the persisted state.
        return


def _validated_artifact_dir(
    data_dir: FilePath, run_id: str, artifact: object
) -> tuple[FilePath, FilePath] | None:
    if not isinstance(artifact, str) or not artifact:
        return None
    try:
        data_root = data_dir.resolve(strict=True)
        work_path = data_root / "work"
        if work_path.is_symlink():
            return None
        work_root = work_path.resolve(strict=True)
        if work_root == data_root or not work_root.is_relative_to(data_root):
            return None
        expected_path = work_path / run_id
        if expected_path.is_symlink():
            return None
        expected_dir = expected_path.resolve(strict=True)
        artifact_dir = FilePath(artifact).resolve(strict=True)
    except OSError:
        return None
    if (
        artifact_dir != expected_dir
        or not artifact_dir.is_relative_to(work_root)
        or not artifact_dir.is_dir()
    ):
        return None
    return work_root, artifact_dir


def _read_review_json(artifact_dir: FilePath, filename: str) -> dict | None:
    candidate = artifact_dir / filename
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if (
            resolved.parent != artifact_dir
            or not resolved.is_relative_to(artifact_dir)
            or not stat.S_ISREG(resolved.stat().st_mode)
        ):
            return None
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _review_metadata(data_dir: FilePath, run_id: str, slot: dict) -> dict | None:
    if slot.get("state") not in {"review_ready", "approved", "held"}:
        return None
    artifact = slot.get("artifact_path")
    if not artifact:
        return None
    validated = _validated_artifact_dir(data_dir, run_id, artifact)
    if validated is None:
        return None
    _, actual = validated

    review = {}
    for key, filename in (("script", "script.json"), ("package", "prepared.json")):
        value = _read_review_json(actual, filename)
        if value is None:
            continue
        if key == "script":
            allowed = {
                "format", "title", "description", "tags", "hook", "scenes",
                "cta", "total_duration_sec",
            }
        else:
            allowed = {"run_id", "scheduled_at", "prepared_at", "quality_gate"}
        review[key] = _sanitize(
            {name: item for name, item in value.items() if name in allowed}
        )
    return review or None


@router.get("")
def slots(day: date = Query(alias="date")):
    cards = list_slot_cards(_data_dir(), day, _now())
    return {"date": day.isoformat(), "slots": [_public_slot(card) for card in cards]}


@router.post(
    "/{run_id}/check-topic", status_code=202,
    dependencies=[Depends(require_dashboard_token)],
)
def check_topic(run_id: RunId, body: TopicCheckRequest, tasks: BackgroundTasks):
    data_dir = _data_dir()
    _call_service(create_check, data_dir, run_id, body.model_dump(), _now())
    tasks.add_task(_run_topic_check_job, data_dir, run_id, body.to_domain())
    return {"accepted": True, "run_id": run_id, "state": "checking"}


@router.put("/{run_id}/reservation", dependencies=[Depends(require_dashboard_token)])
def reserve(run_id: RunId, body: ReservationRequest):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    return _public_slot(_call_service(reserve_checked_topic, data_dir, run_id, _now()))


@router.delete("/{run_id}/reservation", dependencies=[Depends(require_dashboard_token)])
def cancel_reservation(run_id: RunId):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    return _call_service(cancel_manual_reservation, data_dir, run_id, _now())


@router.get("/{run_id}")
def slot_detail(run_id: RunId):
    data_dir = _data_dir()
    stored = _manual_slot_or_404(data_dir, run_id)
    result = _public_slot(stored)
    result["input_open"] = _now() < datetime.fromisoformat(stored["production_at"])
    review = _review_metadata(data_dir, run_id, stored)
    if review is not None:
        result["review"] = review
    return result


@router.get("/{run_id}/events")
def slot_events(
    run_id: RunId,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=0, le=100),
):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    events = (
        public
        for event in events_after(data_dir, run_id, after_id, limit)
        if (public := _public_event(event, run_id)) is not None
    )
    return {"run_id": run_id, "events": list(events)}


@router.get("/{run_id}/video", dependencies=[Depends(require_dashboard_token)])
def slot_video(run_id: RunId):
    data_dir = _data_dir()
    slot = _manual_slot_or_404(data_dir, run_id)
    if slot.get("state") not in {"review_ready", "approved", "held"}:
        raise HTTPException(status_code=404, detail="검토 가능한 영상이 없습니다")
    artifact = slot.get("artifact_path")
    if not artifact:
        raise HTTPException(status_code=404, detail="영상 산출물이 없습니다")
    validated = _validated_artifact_dir(data_dir, run_id, artifact)
    if validated is None:
        raise HTTPException(status_code=404, detail="영상 산출물이 없습니다")
    work_root, artifact_dir = validated
    try:
        video_path = artifact_dir / "output.mp4"
        if video_path.is_symlink():
            raise OSError("symlinked video")
        video = video_path.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="영상 산출물이 없습니다") from exc
    if (
        not video.is_relative_to(work_root)
        or video.parent != artifact_dir
        or not video.is_file()
    ):
        raise HTTPException(status_code=404, detail="영상 산출물이 없습니다")
    return FileResponse(video, media_type="video/mp4", filename=f"{run_id}.mp4")


@router.post("/{run_id}/approve", dependencies=[Depends(require_dashboard_token)])
def approve(run_id: RunId, tasks: BackgroundTasks):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    result = _call_service(approve_slot, data_dir, run_id, _now())
    if result["upload_action"] == "immediate":
        tasks.add_task(run_pipeline, data_dir, _ffmpeg_path(), slot=int(run_id[-1]))
    return _public_slot(result)


@router.post("/{run_id}/reject", dependencies=[Depends(require_dashboard_token)])
def reject(run_id: RunId, body: RejectRequest):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    return _public_slot(_call_service(reject_slot, data_dir, run_id, body.reason, _now()))


@router.post("/{run_id}/retry", dependencies=[Depends(require_dashboard_token)])
def retry(run_id: RunId, body: RetryRequest, tasks: BackgroundTasks):
    data_dir = _data_dir()
    previous = _manual_slot_or_404(data_dir, run_id)
    request = None
    if body.mode == "new_topic":
        request_json = previous.get("request_json")
        if not isinstance(request_json, dict):
            raise HTTPException(status_code=422, detail="재검사할 소재 입력이 없습니다")
        try:
            request = TopicCheckRequest.model_validate(request_json)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="재검사할 소재 입력이 올바르지 않습니다") from exc
    result = _call_service(retry_slot, data_dir, run_id, body.mode, _now())
    if request is not None:
        tasks.add_task(_run_topic_check_job, data_dir, run_id, request.to_domain())
    return _public_slot(result)


@router.post("/{run_id}/skip", dependencies=[Depends(require_dashboard_token)])
def skip(run_id: RunId):
    data_dir = _data_dir()
    _manual_slot_or_404(data_dir, run_id)
    return _public_slot(_call_service(skip_slot, data_dir, run_id, _now()))
