"""오케스트레이터 — 전체 파이프라인 지휘."""
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.content_format import get_content_format
from app.agents.producer import run_producer
from app.agents.researcher import run_researcher
from app.agents.uploader import run_uploader
from app.agents.writer import run_writer


def _output_matches_script(work_dir: Path) -> bool:
    """기존 output.mp4가 '현재 script.json'으로 만들어졌는지 해시로 확인.

    produce_log.json에 기록된 script_sha256과 현재 script.json 해시를 비교.
    다르면(대본 수정됨) 기존 영상은 폐기 대상 → False.
    """
    output = work_dir / "output.mp4"
    script = work_dir / "script.json"
    plog = work_dir / "produce_log.json"
    if not (output.exists() and script.exists() and plog.exists()):
        return False
    try:
        recorded = json.loads(plog.read_text(encoding="utf-8")).get("script_sha256", "")
        current = hashlib.sha256(script.read_bytes()).hexdigest()
        return bool(recorded) and recorded == current
    except (json.JSONDecodeError, OSError):
        return False


def _next_slot(data_dir: Path, date_str: str) -> int:
    """오늘 아직 업로드 안 된 첫 회차 반환 (수동 실행용 자동 회차 선택)."""
    import sqlite3

    uploaded = set()
    db_file = data_dir / "videos.sqlite"
    if db_file.exists():
        db = sqlite3.connect(db_file)
        try:
            rows = db.execute(
                "SELECT date FROM videos WHERE date LIKE ? AND status = 'uploaded'",
                (f"{date_str}%",),
            ).fetchall()
        finally:
            db.close()
        uploaded = {r[0] for r in rows}

    for i in range(1, 7):
        if f"{date_str}-{i}" not in uploaded:
            return i
    return 6


def _load_prepared_marker(work_dir: Path, run_id: str) -> dict | None:
    marker = work_dir / "prepared.json"
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("run_id") != run_id:
        return None
    quality_gate = value.get("quality_gate")
    if not isinstance(quality_gate, dict) or quality_gate.get("passed") is not True:
        return None
    return value


async def run_pipeline(data_dir: Path, ffmpeg_path: str, slot: int = None) -> dict:
    """
    전체 파이프라인 실행: 리서처 → 작가 → 프로듀서 → 업로더

    Args:
        data_dir: 데이터 저장 경로
        ffmpeg_path: ffmpeg 실행 파일 경로
        slot: 오늘의 회차 (cron이 1/4/2/3 지정, None이면 자동 선택)

    Returns:
        run_log dict (각 단계 결과)
    """
    content_format = get_content_format()
    date_str = datetime.now().strftime("%Y%m%d")
    if slot is None:
        slot = _next_slot(data_dir, date_str)
    run_id = f"{date_str}-{slot}"

    work_dir = data_dir / "work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    run_log = {
        "date": run_id,
        "timestamp": datetime.now().isoformat(),
        "content_format": content_format,
        "stages": {},
        "success": True,
        "message": "",
    }
    prepared = _load_prepared_marker(work_dir, run_id)
    if prepared is not None:
        run_log["prepared"] = prepared

    try:
        # 1. 트렌드 리서처 (오늘 결과가 이미 있고 검증 통과 시에만 재사용)
        from app.models import validate_topic, validate_script

        topic_file = work_dir / "topic.json"
        topic = None
        if topic_file.exists():
            try:
                topic = validate_topic(
                    json.loads(topic_file.read_text(encoding="utf-8")), content_format
                )
                run_log["stages"]["researcher"] = {"status": "skipped", "topic": topic.get("topic", "")}
                print(f"[1/4] 리서처 건너뜀 (오늘 소재 이미 있음: {topic.get('topic', '')})")
            except Exception as e:
                print(f"[1/4] 기존 topic.json 검증 실패({e}) — 재생성")
        if topic is None:
            print("[1/4] 트렌드 리서처 실행 중...")
            topic = run_researcher(data_dir, run_id, content_format=content_format)
            run_log["stages"]["researcher"] = {
                "status": "success",
                "topic": topic.get("topic", ""),
                "items_count": len(topic.get("items", [])),
            }
            print(f"✓ 소재 선정: {topic['topic']}")

        # 2. 대본 작가 (오늘 대본이 이미 있고 검증 통과 시에만 재사용)
        script_file = work_dir / "script.json"
        script = None
        if script_file.exists():
            try:
                saved_script = json.loads(script_file.read_text(encoding="utf-8"))
                script = validate_script(
                    saved_script, content_format
                )
                if saved_script.get("writer_mode") in {"llm", "llm_retry", "verified_template"}:
                    script["writer_mode"] = saved_script["writer_mode"]
                run_log["stages"]["writer"] = {
                    "status": "skipped",
                    "title": script.get("title", ""),
                    "writer_mode": script.get("writer_mode", "legacy"),
                }
                print(f"[2/4] 작가 건너뜀 (오늘 대본 이미 있음: {script.get('title', '')})")
            except Exception as e:
                print(f"[2/4] 기존 script.json 검증 실패({e}) — 재생성")
        if script is None:
            print("[2/4] 대본 작가 실행 중...")
            script = run_writer(data_dir, run_id, content_format=content_format)
            run_log["stages"]["writer"] = {
                "status": "success",
                "title": script.get("title", ""),
                "scenes_count": len(script.get("scenes", [])),
                "total_duration": script.get("total_duration_sec", 0),
                "writer_mode": script.get("writer_mode", "legacy"),
            }
            print(f"✓ 대본 생성: {script['title']} ({script.get('total_duration_sec')}초)")

        # 3. 영상 프로듀서 (영상이 있고 '그 영상을 만든 대본'과 현재 대본이 같을 때만 재사용)
        output_file = work_dir / "output.mp4"
        fresh = _output_matches_script(work_dir)
        if output_file.exists() and fresh:
            run_log["stages"]["producer"] = {"status": "skipped", "output_file": str(output_file)}
            print(f"[3/4] 프로듀서 건너뜀 (영상이 현재 대본과 일치)")
        else:
            if output_file.exists() and not fresh:
                print("[3/4] 대본 변경 감지(해시 불일치) — 기존 영상 폐기 후 재생성")
            print("[3/4] 영상 프로듀서 실행 중...")
            produce_log = await run_producer(
                data_dir, run_id, ffmpeg_path, content_format=content_format
            )
            run_log["stages"]["producer"] = {
                "status": "success",
                "output_file": produce_log.get("output_file", ""),
                "duration": produce_log.get("video_duration", 0),
            }
            print(f"✓ 영상 생성: {produce_log.get('output_file')}")

        # 4. 업로더 (중복/한도 체크는 업로더 내부에서 처리)
        print("[4/4] 업로더 실행 중...")
        upload_result = run_uploader(data_dir, run_id)
        run_log["stages"]["uploader"] = upload_result
        if upload_result.get("status") == "uploaded":
            print(f"✓ 업로드 완료: {upload_result.get('url')}")
        else:
            print(f"[4/4] 업로드 건너뜀: {upload_result.get('reason', '')}")

        # 5. 분석가 — 기존 영상 성과 수집 + 카테고리별 리포트 갱신 (실패해도 파이프라인 성공 유지)
        try:
            print("[분석] 성과 리포트 갱신 중...")
            from app.agents.analyst import run_analyst
            report = run_analyst(data_dir)
            run_log["stages"]["analyst"] = {"status": "success", "insight": report.get("insight", "")}
            print(f"  ✓ {report.get('insight', '')}")
        except Exception as e:
            run_log["stages"]["analyst"] = {"status": "error", "error": str(e)}
            print(f"  ⚠️ 분석가 실패(무시): {e}")

        # 메시지를 확정한 뒤 로그 저장 (저장 후 설정하면 파일에 빈 메시지가 남는 버그 방지)
        run_log["message"] = "파이프라인 완료"
        log_file = data_dir / "logs" / f"run-{run_id}.json"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

        return run_log

    except Exception as e:
        run_log["success"] = False
        run_log["message"] = str(e)
        # 실패한 단계 표시 (성공 기록된 단계 다음이 실패 지점)
        stage_order = ["researcher", "writer", "producer", "uploader"]
        done = set(run_log["stages"].keys())
        failed_stage = next((s for s in stage_order if s not in done), "unknown")
        run_log["stages"][failed_stage] = {"status": "error", "error": str(e)}

        log_file = data_dir / "logs" / f"run-{run_id}.json"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")

        raise
