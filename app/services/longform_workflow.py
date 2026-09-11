"""Operator approval workflow for manual longform production."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.agents.longform_producer import create_longform_thumbnail
from app.models import validate_longform_script


RUN_ID_PATTERN = re.compile(r"^longform-[a-z0-9][a-z0-9_-]{2,80}$")
STAGE_STATUS = {
    "preview": "PREVIEW_REQUESTED",
    "full": "FULL_REQUESTED",
    "upload": "UPLOAD_REQUESTED",
}
_SEGMENTS = [
    ("5위 첫 번째 기록", "실제 화면에서 바로 이상함이 보이는 첫 번째 단서"),
    ("4위 두 번째 기록", "공식 기록과 현장 화면이 서로 맞물리는 지점"),
    ("3위 세 번째 기록", "대부분의 사람이 오해하는 핵심 장면"),
    ("2위 네 번째 기록", "규모와 구조가 동시에 드러나는 장면"),
    ("1위 마지막 기록", "가장 강한 반전과 아직 남은 질문"),
]
_ROLES = [
    "hook", "context", "evidence", "mechanism", "evidence",
    "counterpoint", "mechanism", "payoff",
] * 5
_ROLES[-1] = "close"


def _now_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _longform_root(data_dir: Path) -> Path:
    return Path(data_dir) / "longform"


def _safe_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("잘못된 롱폼 작업 ID입니다")
    return run_id


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_from_report(data_dir: Path) -> dict:
    report = _read_json(Path(data_dir) / "reports" / "performance_latest.json")
    candidates = [
        item for item in report.get("longform_candidates", [])
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    if candidates:
        return dict(candidates[0])
    return {
        "title": "지구에서 실제로 관측된 이상한 장소 TOP 5",
        "expansion_brief": "실제 장소와 공식 기록이 남아 있는 지구 미스터리 다섯 가지를 비교한다.",
        "views": 0,
        "pattern_tags": ["실제 장소", "지구 미스터리", "TOP5"],
    }


def _thumbnail_text(title: str) -> tuple[str, str]:
    if "피폭포" in title:
        return "남극의 피폭포", "절대 안 언다?"
    if "TOP" in title.upper():
        return "지구가 숨긴 TOP 5", "이건 진짜 이상함"
    compact = title.replace("진짜 이유", "").replace("비밀", "").strip(" ,:：")
    words = compact.split()
    main = " ".join(words[:3]) if words else compact[:12]
    return main[:24] or "지구의 미스터리", "왜 이게 있지?"


def _sentence(value: str) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        normalized = "현장 화면, 조사 기록, 반론을 함께 보면 단순한 풍경과 다른 지점이 드러납니다"
    return normalized if normalized.endswith((".", "!", "?")) else f"{normalized}."


def _build_script(candidate: dict) -> dict:
    title = str(candidate.get("title") or "지구에서 실제로 관측된 이상한 장소 TOP 5").strip()
    brief = str(candidate.get("expansion_brief") or "").strip()
    tags = [str(tag) for tag in candidate.get("pattern_tags") or [] if str(tag).strip()]
    main, sub = _thumbnail_text(title)
    scenes = []
    for index, role in enumerate(_ROLES, start=1):
        group = min((index - 1) // 8, len(_SEGMENTS) - 1)
        rank = 5 - group
        segment_title, angle = _SEGMENTS[group]
        if index == 1:
            narration = (
                f"오늘의 주제는 {title}입니다. "
                "지금부터 화면으로 확인 가능한 기록만 골라, 왜 사람들이 이 장면을 이상하게 느끼는지 순서대로 보겠습니다."
            )
        elif index in {9, 17, 25, 33}:
            narration = (
                f"다음은 {rank}위 기록입니다. {segment_title}은 {angle} 때문에 그냥 지나치기 어렵습니다."
            )
        elif index == 40:
            narration = (
                f"정리하면 {title}의 핵심은 이상한 장면 자체보다, 기록과 화면이 어디까지 맞물리는지입니다. "
                "다음 영상에서는 더 선명한 지구의 기록을 이어가겠습니다."
            )
        else:
            brief_sentence = _sentence(
                brief
                or "현장 화면, 조사 기록, 반론을 함께 보면 단순한 풍경과 다른 지점이 드러납니다."
            )
            narration = (
                f"{segment_title}에서는 {angle}을 중심으로 봐야 합니다. "
                f"{brief_sentence}"
            )
        scenes.append(
            {
                "n": index,
                "rank": rank,
                "segment_title": segment_title,
                "role": role,
                "chapter_title": segment_title,
                "narration": narration,
                "visuals": [
                    f"{title} documentary landscape footage",
                    f"{segment_title} real location aerial video",
                ],
                "duration_sec": 12,
            }
        )
    script = {
        "format": "longform",
        "title": title[:100],
        "description": f"{title}에 맞는 실제 기록과 화면 자료를 TOP 구성으로 정리합니다.",
        "tags": list(dict.fromkeys(tags + ["이상한 지구기록", "지구 미스터리", "과학 미스터리"])),
        "hook": f"{title}에서 가장 이상한 지점은 무엇일까요?",
        "thumbnail_main": main,
        "thumbnail_sub": sub,
        "style_id": "clean_news",
        "scenes": scenes,
        "cta": "이런 지구의 기록이 더 궁금하다면 구독과 좋아요 부탁드립니다.",
    }
    return validate_longform_script(script)


def _workflow_state(
    *,
    run_id: str,
    status: str,
    topic: dict,
    now: datetime | None = None,
    extra: dict | None = None,
) -> dict:
    state = {
        "run_id": run_id,
        "status": status,
        "topic": topic,
        "updated_at": _now_iso(now),
    }
    if extra:
        state.update(extra)
    return state


def create_longform_draft(data_dir: Path, *, now: datetime | None = None) -> dict:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    run_id = f"longform-{timestamp}"
    candidate = _candidate_from_report(data_dir)
    script = _build_script(candidate)
    run_dir = _longform_root(data_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json(run_dir / "script.json", script)
    create_longform_thumbnail(script, run_dir / "thumbnail.png")
    topic = {
        "title": script["title"],
        "brief": candidate.get("expansion_brief") or script["hook"],
        "source_views": candidate.get("views", 0),
        "pattern_tags": candidate.get("pattern_tags") or script["tags"],
    }
    state = _workflow_state(
        run_id=run_id,
        status="DRAFT_TOPIC",
        topic=topic,
        now=now,
        extra={
            "thumbnail_url": f"/api/longform/{run_id}/thumbnail",
            "next_action": "approve_topic",
        },
    )
    _write_json(run_dir / "workflow.json", state)
    return state


def _default_command_runner(command: list[str], cwd: Path, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = log_file.open("ab")
    subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )


def request_longform_stage(
    data_dir: Path,
    run_id: str,
    stage: str,
    *,
    ffmpeg_path: str = "ffmpeg",
    command_runner: Callable[[list[str], Path, Path], None] | None = None,
) -> dict:
    run_id = _safe_run_id(run_id)
    if stage not in STAGE_STATUS:
        raise ValueError("지원하지 않는 롱폼 단계입니다")
    run_dir = _longform_root(data_dir) / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"롱폼 작업을 찾을 수 없습니다: {run_id}")
    if stage == "upload" and not (run_dir / "output.mp4").is_file():
        raise FileNotFoundError("업로드할 롱폼 output.mp4가 없습니다")

    root = Path(__file__).resolve().parents[2]
    if stage == "preview":
        command = [
            sys.executable, "scripts/run_longform_preview_stage.py",
            "--data-dir", str(Path(data_dir)),
            "--run-id", run_id,
            "--ffmpeg-path", ffmpeg_path,
        ]
    elif stage == "full":
        command = [
            sys.executable, "scripts/generate_longform.py",
            "--data-dir", str(Path(data_dir)),
            "--run-id", run_id,
            "--ffmpeg-path", ffmpeg_path,
        ]
    else:
        command = [
            sys.executable, "scripts/upload_longform.py",
            "--data-dir", str(Path(data_dir)),
            "--run-id", run_id,
        ]

    current = _read_json(run_dir / "workflow.json")
    state = _workflow_state(
        run_id=run_id,
        status=STAGE_STATUS[stage],
        topic=current.get("topic") if isinstance(current.get("topic"), dict) else {},
        extra={"last_requested_stage": stage},
    )
    _write_json(run_dir / "workflow.json", {**current, **state})
    runner = command_runner or _default_command_runner
    runner(command, root, run_dir / "job.log")
    return {**current, **state}


def list_longform_jobs(data_dir: Path) -> list[dict]:
    root = _longform_root(data_dir)
    if not root.exists():
        return []
    jobs = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or not RUN_ID_PATTERN.fullmatch(run_dir.name):
            continue
        workflow = _read_json(run_dir / "workflow.json")
        script = _read_json(run_dir / "script.json")
        upload_log = _read_json(run_dir / "upload_log.json")
        status = workflow.get("status")
        if upload_log.get("status") == "uploaded":
            status = "UPLOADED"
        elif (run_dir / "output.mp4").is_file() and status in {None, "FULL_REQUESTED"}:
            status = "FULL_READY"
        elif (run_dir / "preview_30s.mp4").is_file() and status in {None, "PREVIEW_REQUESTED"}:
            status = "PREVIEW_READY"
        jobs.append({
            "run_id": run_dir.name,
            "status": status or "UNKNOWN",
            "title": script.get("title") or workflow.get("topic", {}).get("title") or run_dir.name,
            "topic": workflow.get("topic") if isinstance(workflow.get("topic"), dict) else {},
            "updated_at": workflow.get("updated_at"),
            "thumbnail_url": f"/api/longform/{run_dir.name}/thumbnail" if (run_dir / "thumbnail.png").is_file() else None,
            "preview_url": f"/api/longform/{run_dir.name}/preview" if (run_dir / "preview_30s.mp4").is_file() else None,
            "output_url": f"/api/longform/{run_dir.name}/output" if (run_dir / "output.mp4").is_file() else None,
            "youtube_url": upload_log.get("url"),
        })
    return sorted(jobs, key=lambda item: item.get("run_id") or "", reverse=True)
