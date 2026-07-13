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

        # 2. 일 업로드 한도 확인 — API 쿼터상 절대 한도 6건을 설정으로도 초과 불가
        limit = min(int(os.getenv("DAILY_UPLOAD_LIMIT", "6")), 6)
        today = datetime.now().strftime("%Y%m%d")
        # date는 회차 포함 형식("20260713-2")이므로 오늘 전체는 LIKE로 집계
        today_count = db.execute(
            "SELECT COUNT(*) FROM videos WHERE date LIKE ? AND status = 'uploaded'",
            (f"{today}%",),
        ).fetchone()[0]
        if today_count >= limit:
            return {"status": "skipped", "reason": f"일 업로드 한도({limit}건) 도달 — 내일 재시도"}

        # 3. 업로드 전 메타데이터 검증
        title = script.get("title", "").strip()
        if not title:
            raise ValueError("script.json에 title이 없습니다")
        if len(title) > 100:
            # 조용히 자르면 SEO 설계가 깨진 채 올라감 — 실패시켜서 재작성 유도
            raise ValueError(f"제목이 100자 초과({len(title)}자) — 업로드 중단")

        # 태그 총 길이 500자 제한 (초과분은 뒤에서부터 제거 — 태그는 보조 신호라 잘라도 안전)
        tags = []
        total_len = 0
        for t in script.get("tags", []):
            if total_len + len(t) > 480:
                break
            tags.append(t)
            total_len += len(t)

        # 4. 영상 파일 검증 (손상/규격 미달 영상이 올라가는 것 차단)
        _validate_video_file(video_file)

        # 5. 채널 설정 로드
        channel_cfg = {}
        cfg_file = Path("config/channel.json")
        if cfg_file.exists():
            channel_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))

        # 6. 업로드 실행
        youtube = _get_youtube_client()

        body = {
            "snippet": {
                "title": title,
                "description": script.get("description", ""),
                "tags": tags[:30],
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

        # 7. DB 기록 (video_id 수신 = 업로드 성공 기준). topic은 분석가의 소재별 성과 비교용.
        topic_str = ""
        topic_file = work_dir / "topic.json"
        if topic_file.exists():
            try:
                topic_str = json.loads(topic_file.read_text(encoding="utf-8")).get("topic", "")
            except (json.JSONDecodeError, OSError):
                pass

        db.execute(
            "INSERT INTO videos (video_id, date, title, topic, status, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, date_str, title, topic_str, "uploaded", datetime.now().isoformat()),
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
    """업로드 기록 DB 초기화 (+구버전 테이블에 topic 컬럼 마이그레이션)."""
    db_file = data_dir / "videos.sqlite"
    db = sqlite3.connect(db_file)
    db.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            title TEXT,
            topic TEXT,
            status TEXT NOT NULL,
            uploaded_at TEXT
        )
    """)
    # 기존 DB에 topic 컬럼이 없으면 추가
    cols = [row[1] for row in db.execute("PRAGMA table_info(videos)")]
    if "topic" not in cols:
        db.execute("ALTER TABLE videos ADD COLUMN topic TEXT")
        db.commit()
    return db


def _validate_video_file(video_file: Path) -> None:
    """업로드 전 영상 규격 검증 — 하나라도 실패하면 업로드 중단."""
    import subprocess

    from app.agents.producer import _ffprobe_path

    ffprobe = _ffprobe_path(os.getenv("FFMPEG_PATH", "ffmpeg"))
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video_file)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"영상 파일 판독 불가 (손상 의심): {video_file}")

    info = json.loads(result.stdout)

    duration = float(info.get("format", {}).get("duration", 0))
    if not 5 <= duration < 60:
        raise ValueError(f"영상 길이 {duration:.1f}초 — 숏츠 조건(5~60초) 위반")

    video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise ValueError("비디오 스트림 없음")
    if (video_stream.get("width"), video_stream.get("height")) != (1080, 1920):
        raise ValueError(
            f"해상도 {video_stream.get('width')}x{video_stream.get('height')} — 1080x1920 아님"
        )

    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("오디오 스트림 없음 (나레이션 누락)")


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
