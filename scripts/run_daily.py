"""매일 자동 실행용 단독 스크립트 — 서버/브라우저 없이 파이프라인 1회 실행.

사용법:
    cd D:\\ms\\shorts-factory-be
    python scripts\\run_daily.py

Windows 작업 스케줄러 / 리눅스 cron이 이 파일을 직접 호출한다.
"""
import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 (어디서 실행해도 동작)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # .env, config/, credentials/ 상대경로 기준점 고정

from dotenv import load_dotenv

load_dotenv()

from app.agents.orchestrator import run_pipeline  # noqa: E402


def cleanup_old_work(data_dir: Path, keep_days: int = 7):
    """오래된 작업 폴더 삭제 — 영상은 유튜브에 있으므로 로컬 보관 불필요 (디스크 관리)."""
    import shutil
    from datetime import datetime, timedelta

    work = data_dir / "work"
    if not work.exists():
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    for d in work.iterdir():
        # 폴더명: "20260713" (구형식) 또는 "20260713-2" (회차 형식)
        date_part = d.name[:8]
        if d.is_dir() and date_part.isdigit():
            try:
                if datetime.strptime(date_part, "%Y%m%d") < cutoff:
                    shutil.rmtree(d)
                    print(f"오래된 작업 폴더 삭제: {d.name}")
            except ValueError:
                pass


def main():
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")

    # 회차: cron이 인자로 지정 (12시=1, 15시=2, 18시=3). 없으면 자동 선택.
    slot = int(sys.argv[1]) if len(sys.argv) > 1 else None

    cleanup_old_work(data_dir, int(os.getenv("WORK_RETENTION_DAYS", "7")))

    try:
        result = asyncio.run(run_pipeline(data_dir, ffmpeg_path, slot=slot))
        uploader = result.get("stages", {}).get("uploader", {})
        print("\n===== 오늘의 실행 결과 =====")
        print(f"성공 여부: {result.get('success')}")
        if uploader.get("status") == "uploaded":
            print(f"업로드: {uploader.get('url')}")
        else:
            print(f"업로드: {uploader.get('reason', uploader.get('status', '알 수 없음'))}")
        sys.exit(0)
    except Exception as e:
        print(f"\n===== 파이프라인 실패 =====\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
