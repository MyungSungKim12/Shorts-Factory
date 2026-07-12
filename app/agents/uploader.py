"""업로더 에이전트 — YouTube Data API v3 자동 업로드."""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CRED_DIR = Path("credentials")


def run_uploader(data_dir: Path, date_str: str) -> dict:
    """
    output.mp4를 YouTube에 업로드하고 DB에 기록한다.

    Returns:
        {"status": "uploaded"|"skipped", "video_id": ..., ...}
    """
    work_dir = data_dir / "work" / date_str
    video_file = work_dir / "output.mp4"
    script_file = work_dir / "script.json"

    if not video_file.exists():
        raise FileNotFoundError(f"업로드할 영상이 없습니다: {video_file}")
    if not script_file.exists():
        raise FileNotFoundError(f"메타데이터(script.json)가 없습니다: {script_file}")

    script = json.loads(script_file.read_text(encoding="utf-8"))
    db = _init_db(data_dir)

    try:
        # 1. 오늘 이 영상이 이미 업로드됐으면 건너뜀 (중복 업로드 방지)
        existing = db.execute(
            "SELECT video_id FROM videos WHERE date = ? AND status = 'uploaded'",
            (date_str,),
        ).fetchone()
        if existing:
            return {"status": "skipped", "video_id": existing[0], "reason": "오늘 영상 이미 업로드됨"}

        # 2. 일 업로드 한도 확인 (YouTube API 쿼터 보호)
        limit = int(os.getenv("DAILY_UPLOAD_LIMIT", "6"))
        today = datetime.now().strftime("%Y%m%d")
        today_count = db.execute(
            "SELECT COUNT(*) FROM videos WHERE date = ? AND status = 'uploaded'",
            (today,),
        ).fetchone()[0]
        if today_count >= limit:
            return {"status": "skipped", "reason": f"일 업로드 한도({limit}건) 도달 — 내일 재시도"}

        # 3. 업로드 전 검증
        title = script.get("title", "").strip()
        if not title:
            raise ValueError("script.json에 title이 없습니다")
        title = title[:100]  # YouTube 제목 100자 제한

        # 4. 채널 설정 로드
        channel_cfg = {}
        cfg_file = Path("config/channel.json")
        if cfg_file.exists():
            channel_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))

        # 5. 업로드 실행
        youtube = _get_youtube_client()

        body = {
            "snippet": {
                "title": title,
                "description": script.get("description", ""),
                "tags": script.get("tags", [])[:30],
                "categoryId": str(channel_cfg.get("category_id", "24")),
                "defaultLanguage": "ko",
            },
            "status": {
                "privacyStatus": os.getenv("UPLOAD_PRIVACY", "unlisted"),
                "selfDeclaredMadeForKids": bool(channel_cfg.get("made_for_kids", False)),
            },
        }

        media = MediaFileUpload(str(video_file), mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response["id"]

        # 6. DB 기록 (video_id 수신 = 업로드 성공 기준)
        db.execute(
            "INSERT INTO videos (video_id, date, title, status, uploaded_at) VALUES (?, ?, ?, ?, ?)",
            (video_id, date_str, title, "uploaded", datetime.now().isoformat()),
        )
        db.commit()

        return {
            "status": "uploaded",
            "video_id": video_id,
            "url": f"https://youtube.com/shorts/{video_id}",
            "privacy": body["status"]["privacyStatus"],
        }
    finally:
        db.close()


def _init_db(data_dir: Path) -> sqlite3.Connection:
    """업로드 기록 DB 초기화."""
    db_file = data_dir / "videos.sqlite"
    db = sqlite3.connect(db_file)
    db.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT
        )
    """)
    return db


def _get_youtube_client():
    """YouTube API 클라이언트 생성 (최초 1회 브라우저 인증 → 이후 토큰 재사용)."""
    creds = None
    token_file = CRED_DIR / "token.json"
    secret_file = CRED_DIR / "client_secret.json"

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secret_file.exists():
                raise FileNotFoundError(
                    f"OAuth 클라이언트 파일이 없습니다: {secret_file}\n"
                    "Google Cloud Console에서 다운로드한 JSON을 이 경로에 저장하세요."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
            # 브라우저가 자동으로 열림 — 채널 소유 구글 계정으로 로그인
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)
