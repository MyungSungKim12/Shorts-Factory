"""Dashboard API for manual longform approval workflow."""
from __future__ import annotations

import json
from pathlib import Path as FilePath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.routes.slots import require_dashboard_token
from app.services.longform_workflow import (
    RUN_ID_PATTERN,
    create_longform_draft,
    list_longform_jobs,
    regenerate_longform_thumbnail,
    request_longform_stage,
)


router = APIRouter(prefix="/api/longform", tags=["longform"])


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=300)


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["preview", "full", "upload"]


def _data_dir() -> FilePath:
    from app import main

    return FilePath(main.DATA_DIR)


def _ffmpeg_path() -> str:
    from app import main

    return str(main.FFMPEG_PATH)


def _safe_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="롱폼 작업을 찾을 수 없습니다")
    return run_id


def _workflow_path(data_dir: FilePath, run_id: str) -> FilePath:
    return data_dir / "longform" / run_id / "workflow.json"


def _read_workflow(data_dir: FilePath, run_id: str) -> dict:
    path = _workflow_path(data_dir, run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {"run_id": run_id}
    return value if isinstance(value, dict) else {"run_id": run_id}


def _write_workflow(data_dir: FilePath, run_id: str, workflow: dict) -> dict:
    path = _workflow_path(data_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return workflow


def _file_response(run_id: str, filename: str, media_type: str):
    data_dir = _data_dir()
    run_id = _safe_run_id(run_id)
    path = (data_dir / "longform" / run_id / filename).resolve()
    root = (data_dir / "longform" / run_id).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("", dependencies=[Depends(require_dashboard_token)])
def longform_jobs():
    return {"jobs": list_longform_jobs(_data_dir())}


@router.post("", dependencies=[Depends(require_dashboard_token)])
def longform_create_draft():
    return create_longform_draft(_data_dir())


@router.post("/{run_id}/approve-topic", dependencies=[Depends(require_dashboard_token)])
def longform_approve_topic(run_id: str = Path(...)):
    try:
        return request_longform_stage(
            _data_dir(),
            _safe_run_id(run_id),
            "preview",
            ffmpeg_path=_ffmpeg_path(),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{run_id}/thumbnail", dependencies=[Depends(require_dashboard_token)])
def longform_regenerate_thumbnail(run_id: str = Path(...)):
    try:
        return regenerate_longform_thumbnail(_data_dir(), _safe_run_id(run_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{run_id}/reject-topic", dependencies=[Depends(require_dashboard_token)])
def longform_reject_topic(request: RejectRequest, run_id: str = Path(...)):
    data_dir = _data_dir()
    run_id = _safe_run_id(run_id)
    workflow = _read_workflow(data_dir, run_id)
    workflow.update({"status": "TOPIC_REJECTED", "rejection_reason": request.reason})
    return _write_workflow(data_dir, run_id, workflow)


@router.post("/{run_id}/approve-preview", dependencies=[Depends(require_dashboard_token)])
def longform_approve_preview(run_id: str = Path(...)):
    try:
        return request_longform_stage(
            _data_dir(),
            _safe_run_id(run_id),
            "full",
            ffmpeg_path=_ffmpeg_path(),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{run_id}/reject-preview", dependencies=[Depends(require_dashboard_token)])
def longform_reject_preview(request: RejectRequest, run_id: str = Path(...)):
    data_dir = _data_dir()
    run_id = _safe_run_id(run_id)
    workflow = _read_workflow(data_dir, run_id)
    workflow.update({"status": "PREVIEW_REJECTED", "rejection_reason": request.reason})
    return _write_workflow(data_dir, run_id, workflow)


@router.post("/{run_id}/upload", dependencies=[Depends(require_dashboard_token)])
def longform_upload(run_id: str = Path(...)):
    try:
        return request_longform_stage(_data_dir(), _safe_run_id(run_id), "upload")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{run_id}/thumbnail", dependencies=[Depends(require_dashboard_token)])
def longform_thumbnail(run_id: str = Path(...)):
    return _file_response(run_id, "thumbnail.png", "image/png")


@router.get("/{run_id}/preview", dependencies=[Depends(require_dashboard_token)])
def longform_preview(run_id: str = Path(...)):
    return _file_response(run_id, "preview_30s.mp4", "video/mp4")


@router.get("/{run_id}/output", dependencies=[Depends(require_dashboard_token)])
def longform_output(run_id: str = Path(...)):
    return _file_response(run_id, "output.mp4", "video/mp4")
