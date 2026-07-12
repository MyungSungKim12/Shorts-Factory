"""오케스트레이터 — 전체 파이프라인 지휘."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.agents.producer import run_producer
from app.agents.researcher import run_researcher
from app.agents.uploader import run_uploader
from app.agents.writer import run_writer


async def run_pipeline(data_dir: Path, ffmpeg_path: str) -> dict:
    """
    전체 파이프라인 실행: 리서처 → 작가 → 프로듀서 → 업로더

    Args:
        data_dir: 데이터 저장 경로
        ffmpeg_path: ffmpeg 실행 파일 경로

    Returns:
        run_log dict (각 단계 결과)
    """
    date_str = datetime.now().strftime("%Y%m%d")
    work_dir = data_dir / "work" / date_str
    work_dir.mkdir(parents=True, exist_ok=True)

    run_log = {
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "stages": {},
        "success": True,
        "message": "",
    }

    try:
        # 1. 트렌드 리서처 (오늘 결과가 이미 있으면 건너뜀 — API 호출 절약)
        topic_file = work_dir / "topic.json"
        if topic_file.exists():
            topic = json.loads(topic_file.read_text(encoding="utf-8"))
            run_log["stages"]["researcher"] = {"status": "skipped", "topic": topic.get("topic", "")}
            print(f"[1/4] 리서처 건너뜀 (오늘 소재 이미 있음: {topic.get('topic', '')})")
        else:
            print("[1/4] 트렌드 리서처 실행 중...")
            topic = run_researcher(data_dir)
            run_log["stages"]["researcher"] = {
                "status": "success",
                "topic": topic.get("topic", ""),
                "items_count": len(topic.get("items", [])),
            }
            print(f"✓ 소재 선정: {topic['topic']}")

        # 2. 대본 작가 (오늘 대본이 이미 있으면 건너뜀)
        script_file = work_dir / "script.json"
        if script_file.exists():
            script = json.loads(script_file.read_text(encoding="utf-8"))
            run_log["stages"]["writer"] = {"status": "skipped", "title": script.get("title", "")}
            print(f"[2/4] 작가 건너뜀 (오늘 대본 이미 있음: {script.get('title', '')})")
        else:
            print("[2/4] 대본 작가 실행 중...")
            script = run_writer(data_dir, date_str)
            run_log["stages"]["writer"] = {
                "status": "success",
                "title": script.get("title", ""),
                "scenes_count": len(script.get("scenes", [])),
                "total_duration": script.get("total_duration_sec", 0),
            }
            print(f"✓ 대본 생성: {script['title']} ({script.get('total_duration_sec')}초)")

        # 3. 영상 프로듀서 (오늘 영상이 이미 있으면 건너뜀)
        output_file = work_dir / "output.mp4"
        if output_file.exists():
            run_log["stages"]["producer"] = {"status": "skipped", "output_file": str(output_file)}
            print(f"[3/4] 프로듀서 건너뜀 (오늘 영상 이미 있음)")
        else:
            print("[3/4] 영상 프로듀서 실행 중...")
            produce_log = await run_producer(data_dir, date_str, ffmpeg_path)
            run_log["stages"]["producer"] = {
                "status": "success",
                "output_file": produce_log.get("output_file", ""),
                "duration": produce_log.get("video_duration", 0),
            }
            print(f"✓ 영상 생성: {produce_log.get('output_file')}")

        # 4. 업로더 (중복/한도 체크는 업로더 내부에서 처리)
        print("[4/4] 업로더 실행 중...")
        upload_result = run_uploader(data_dir, date_str)
        run_log["stages"]["uploader"] = upload_result
        if upload_result.get("status") == "uploaded":
            print(f"✓ 업로드 완료: {upload_result.get('url')}")
        else:
            print(f"[4/4] 업로드 건너뜀: {upload_result.get('reason', '')}")

        # 최종 로그 저장
        log_file = data_dir / "logs" / f"run-{date_str}.json"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

        run_log["message"] = "파이프라인 성공 (업로드 제외)"
        return run_log

    except Exception as e:
        run_log["success"] = False
        run_log["message"] = str(e)
        # 실패한 단계 표시 (성공 기록된 단계 다음이 실패 지점)
        stage_order = ["researcher", "writer", "producer", "uploader"]
        done = set(run_log["stages"].keys())
        failed_stage = next((s for s in stage_order if s not in done), "unknown")
        run_log["stages"][failed_stage] = {"status": "error", "error": str(e)}

        log_file = data_dir / "logs" / f"run-{date_str}.json"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

        raise
