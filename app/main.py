"""Shorts Factory 백엔드 — 파이프라인 상태/실행/성과를 대시보드에 제공하는 API."""
import asyncio
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Depends, Query
from fastapi.middleware.cors import CORSMiddleware

from app.agents.orchestrator import run_pipeline
from app.routes.slots import require_dashboard_token, router as slots_router

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

app = FastAPI(title="Shorts Factory API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(slots_router)

# 백그라운드 작업 상태
_pipeline_running = False


def _pagination(page: int, page_size: int, total_items: int) -> dict:
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    return {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_previous": page > 1 and total_pages > 0,
        "has_next": page < total_pages,
    }


_SLOT_RUN_ID = re.compile(r"^\d{8}-([1-4])$")


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _manual_run_ids(db: sqlite3.Connection) -> set[str]:
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='slot_reservations'"
    ).fetchone()
    if table is None:
        return set()
    return {
        str(row[0])
        for row in db.execute(
            "SELECT run_id FROM slot_reservations WHERE mode = 'manual'"
        )
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "running": _pipeline_running}


@app.get("/api/status")
def pipeline_status():
    """오늘 파이프라인 실행 상태 — 가장 최근 회차의 run 로그 반환."""
    logs_dir = DATA_DIR / "logs"
    today = f"{date.today():%Y%m%d}"
    log_files = sorted(logs_dir.glob(f"run-{today}*.json")) if logs_dir.exists() else []
    if log_files:
        return json.loads(log_files[-1].read_text(encoding="utf-8"))
    return {"date": today, "run": None, "message": "오늘 실행 기록 없음"}


@app.get("/api/videos")
def list_videos(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    """업로드된 영상 목록 (업로더가 기록한 SQLite 조회)."""
    import sqlite3

    db_file = DATA_DIR / "videos.sqlite"
    if not db_file.exists():
        return {"videos": [], "pagination": _pagination(page, page_size, 0)}

    db = sqlite3.connect(db_file)
    try:
        total_items = db.execute(
            "SELECT COUNT(*) FROM videos WHERE status = 'uploaded'"
        ).fetchone()[0]
        offset = (page - 1) * page_size
        rows = db.execute(
            "SELECT video_id, date, title, status, uploaded_at FROM videos "
            "WHERE status = 'uploaded' "
            "ORDER BY uploaded_at DESC, video_id DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
    finally:
        db.close()

    return {
        "videos": [
            {
                "video_id": r[0], "date": r[1], "title": r[2],
                "status": r[3], "uploaded_at": r[4],
                "url": f"https://youtube.com/shorts/{r[0]}",
            }
            for r in rows
        ],
        "pagination": _pagination(page, page_size, total_items),
    }


@app.get("/api/history")
def run_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    """최근 14일 파이프라인 실행 이력."""
    logs_dir = DATA_DIR / "logs"
    if not logs_dir.exists():
        return {"runs": [], "pagination": _pagination(page, page_size, 0)}

    runs = []
    for f in logs_dir.glob("run-*.json"):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("date", ""))
            recovery_file = DATA_DIR / "recovery" / f"{run_id}.json"
            if run_id and recovery_file.exists():
                try:
                    recovery = json.loads(recovery_file.read_text(encoding="utf-8"))
                    if isinstance(recovery, dict):
                        run["recovery"] = recovery
                except (json.JSONDecodeError, OSError):
                    pass
            runs.append(run)
        except (json.JSONDecodeError, OSError):
            continue
    runs.sort(
        key=lambda run: (str(run.get("timestamp", "")), str(run.get("date", ""))),
        reverse=True,
    )
    total_items = len(runs)
    offset = (page - 1) * page_size
    return {
        "runs": runs[offset:offset + page_size],
        "pagination": _pagination(page, page_size, total_items),
    }


@app.get("/api/auto-topics")
def automatic_topic_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    """Return persisted automatic topics, including prepared and uploaded slots."""
    entries: dict[str, dict] = {}
    manual_ids: set[str] = set()
    database = DATA_DIR / "videos.sqlite"
    if database.exists():
        with sqlite3.connect(database) as db:
            manual_ids = _manual_run_ids(db)
            videos_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
            ).fetchone()
            if videos_table is not None:
                for run_id, title, topic, status, uploaded_at in db.execute(
                    "SELECT date, title, topic, status, uploaded_at FROM videos "
                    "WHERE status = 'uploaded'"
                ):
                    run_id = str(run_id)
                    if run_id in manual_ids:
                        continue
                    match = _SLOT_RUN_ID.fullmatch(run_id)
                    entries[run_id] = {
                        "run_id": run_id,
                        "slot": int(match.group(1)) if match else None,
                        "topic": topic or title or "",
                        "title": title or topic or "",
                        "status": status,
                        "generated_at": None,
                        "uploaded_at": uploaded_at,
                    }

    work_root = DATA_DIR / "work"
    if work_root.exists():
        for work in work_root.iterdir():
            if not work.is_dir() or not _SLOT_RUN_ID.fullmatch(work.name):
                continue
            run_id = work.name
            if run_id in manual_ids:
                continue
            topic = _json_object(work / "topic.json")
            script = _json_object(work / "script.json")
            prepared = _json_object(work / "prepared.json")
            match = _SLOT_RUN_ID.fullmatch(run_id)
            current = entries.get(run_id, {})
            entries[run_id] = {
                "run_id": run_id,
                "slot": int(match.group(1)),
                "topic": topic.get("topic") or current.get("topic") or "",
                "title": script.get("title") or current.get("title") or "",
                "status": current.get("status") or "prepared",
                "generated_at": prepared.get("prepared_at"),
                "uploaded_at": current.get("uploaded_at"),
            }

    topics = sorted(entries.values(), key=lambda item: item["run_id"], reverse=True)
    total_items = len(topics)
    offset = (page - 1) * page_size
    return {
        "topics": topics[offset:offset + page_size],
        "pagination": _pagination(page, page_size, total_items),
    }


@app.get("/api/report")
def latest_report():
    """분석가 에이전트가 생성한 최신 성과 리포트."""
    report_file = DATA_DIR / "reports" / "latest.json"
    if report_file.exists():
        return json.loads(report_file.read_text(encoding="utf-8"))
    return {"message": "리포트 없음 — 업로드 24시간 후 분석가 에이전트가 생성"}


@app.post("/api/pipeline/run", dependencies=[Depends(require_dashboard_token)])
def trigger_pipeline(background_tasks: BackgroundTasks):
    """파이프라인 수동 실행 트리거 (DASHBOARD_TOKEN 설정 시 토큰 필요)."""
    global _pipeline_running

    if _pipeline_running:
        return {"accepted": False, "message": "파이프라인이 이미 실행 중입니다"}

    _pipeline_running = True

    async def run():
        global _pipeline_running
        try:
            await run_pipeline(DATA_DIR, FFMPEG_PATH)
        finally:
            _pipeline_running = False

    background_tasks.add_task(asyncio.run, run())

    return {
        "accepted": True,
        "message": "파이프라인 실행 시작 (백그라운드에서 진행 중)"
    }
