"""Shorts Factory 백엔드 — 파이프라인 상태/실행/성과를 대시보드에 제공하는 API."""
import asyncio
import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.agents.orchestrator import run_pipeline

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
            runs.append(json.loads(f.read_text(encoding="utf-8")))
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


@app.get("/api/report")
def latest_report():
    """분석가 에이전트가 생성한 최신 성과 리포트."""
    report_file = DATA_DIR / "reports" / "latest.json"
    if report_file.exists():
        return json.loads(report_file.read_text(encoding="utf-8"))
    return {"message": "리포트 없음 — 업로드 24시간 후 분석가 에이전트가 생성"}


@app.post("/api/pipeline/run")
def trigger_pipeline(background_tasks: BackgroundTasks, x_token: str = Header(default="")):
    """파이프라인 수동 실행 트리거 (DASHBOARD_TOKEN 설정 시 토큰 필요)."""
    global _pipeline_running

    # 공개 서버 보호 — 조회는 누구나, 실행은 토큰 소유자만 (fail-closed:
    # 토큰 미설정 시 열리는 게 아니라 실행 자체를 차단)
    token = os.getenv("DASHBOARD_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN 미설정 — 원격 실행이 차단되어 있습니다")
    if x_token != token:
        raise HTTPException(status_code=401, detail="관리 토큰이 필요합니다")

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
