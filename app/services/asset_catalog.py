"""Build a compact, production-backed topic catalog from saved AI assets."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from app.services.ai_opening_library import normalize_subject_key
from app.services.research_feedback import topic_duplicate_reason


_CATEGORY_WEIGHT = {
    "지하시설": 14,
    "지하도시": 14,
    "지하유적": 13,
    "폐시설": 13,
    "폐도시": 12,
    "도시구조": 11,
    "구조물": 11,
    "유적": 10,
    "동굴": 10,
    "기상": 9,
    "지열": 8,
    "지형": 7,
    "대기광학": 6,
    "생태": 4,
}
_ALIAS_GENERIC_TOKENS = {
    "지하", "도시", "동굴", "터널", "광산", "시설", "유적", "구조물",
    "폐허", "폭풍", "구름", "번개", "사막", "호수", "산", "바위",
    "레이더", "성당", "계단", "거리", "지형", "장소", "고대", "거대",
    "underground", "city", "cave", "mine", "ruins", "radar", "mountain",
    "stone", "forest", "desert", "lake", "storm", "wall",
}
_KOREAN_PARTICLE_SUFFIXES = (
    "으로", "에서", "에게", "까지", "부터", "처럼", "보다", "하고",
    "의", "에", "가", "이", "은", "는", "을", "를", "와", "과", "로",
)


def _normalize_alias_token(token: str) -> str:
    normalized = token.casefold()
    for suffix in _KOREAN_PARTICLE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
            return normalized[:-len(suffix)]
    return normalized


def _is_distinctive_alias_token(token: str) -> bool:
    if re.fullmatch(r"[가-힣]+", token):
        return len(token) >= 3
    return len(token) >= 4


def _resolve_asset_path(data_dir: Path, value: object) -> Path:
    path = Path(str(value or ""))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == data_dir.name:
        return data_dir.parent / path
    return data_dir / path


def _ready_assets_by_subject(data_dir: Path) -> dict[str, list[dict]]:
    db_file = data_dir / "videos.sqlite"
    if not db_file.is_file():
        return {}
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row
    try:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_opening_assets'"
        ).fetchone()
        if not exists:
            return {}
        rows = db.execute(
            """
            SELECT subject_key, asset_id, reference_path, master_path, opening_path,
                   source_url, license, model, validation_json
            FROM ai_opening_assets
            WHERE status='ready' AND reuse_scope='exact_subject'
            """
        ).fetchall()
    finally:
        db.close()

    result: dict[str, list[dict]] = {}
    for row in rows:
        paths = [
            _resolve_asset_path(data_dir, row["reference_path"]),
            _resolve_asset_path(data_dir, row["master_path"]),
            _resolve_asset_path(data_dir, row["opening_path"]),
        ]
        if not all(path.is_file() for path in paths):
            continue
        try:
            validation = json.loads(row["validation_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            validation = {}
        result.setdefault(str(row["subject_key"]), []).append({
            "asset_id": str(row["asset_id"]),
            "source_url": str(row["source_url"] or ""),
            "license": str(row["license"] or ""),
            "model": str(row["model"] or ""),
            "validation": validation,
        })
    return result


def _batch_subjects(data_dir: Path) -> dict[str, dict]:
    state_file = data_dir / "media" / "ai_asset_bank" / "batch_state.json"
    if not state_file.is_file():
        return {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}

    subjects: dict[str, dict] = {}
    for entry in (state.get("jobs") or {}).values():
        job = entry.get("job") or {}
        subject = job.get("subject") or {}
        exact_query = str(subject.get("exact_query") or "").strip()
        if not exact_query:
            continue
        subject_key = normalize_subject_key(exact_query)
        item = subjects.setdefault(subject_key, {
            "subject_key": subject_key,
            "bank_key": str(subject.get("key") or subject_key),
            "title_ko": str(subject.get("title_ko") or exact_query),
            "exact_query": exact_query,
            "category": str(subject.get("category") or "기타"),
            "image_completed": False,
        })
        if job.get("kind") == "image" and entry.get("status") == "completed":
            item["image_completed"] = True
    return subjects


def _candidate_duplicate(candidate: dict, avoid_subjects: Iterable[str]) -> bool:
    payload = {
        "topic": candidate.get("title_ko", ""),
        "title": candidate.get("title_ko", ""),
        "hook_angle": candidate.get("category", ""),
        "target_keyword": candidate.get("exact_query", ""),
        "core_question": candidate.get("title_ko", ""),
    }
    prior_values = [str(value or "") for value in avoid_subjects]
    if topic_duplicate_reason(payload, prior_values) is not None:
        return True

    title = str(candidate.get("title_ko") or "").casefold()
    compact_title = re.sub(r"[^0-9a-z가-힣]", "", title)
    subject_tokens = {
        _normalize_alias_token(token)
        for token in re.findall(
            r"[0-9A-Za-z가-힣]{2,}",
            f"{candidate.get('title_ko', '')} {candidate.get('exact_query', '')}",
        )
        if _normalize_alias_token(token) not in _ALIAS_GENERIC_TOKENS
    }
    for prior in prior_values:
        prior_folded = prior.casefold()
        prior_compact = re.sub(r"[^0-9a-z가-힣]", "", prior_folded)
        if len(compact_title) >= 3 and compact_title in prior_compact:
            return True
        prior_tokens = {
            _normalize_alias_token(token)
            for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", prior_folded)
        }
        overlap = subject_tokens & prior_tokens
        if len(overlap) >= 2 or any(
            _is_distinctive_alias_token(token) for token in overlap
        ):
            return True
    return False


def rank_asset_backed_candidates(
    data_dir: Path,
    *,
    avoid_subjects: Iterable[str] = (),
    limit: int = 20,
) -> list[dict]:
    """Return unused subjects that have verified, readable AI video assets."""
    data_dir = Path(data_dir)
    subjects = _batch_subjects(data_dir)
    ready_assets = _ready_assets_by_subject(data_dir)
    candidates = []
    for subject_key, subject in subjects.items():
        assets = ready_assets.get(subject_key) or []
        if not assets:
            continue
        candidate = dict(subject)
        if _candidate_duplicate(candidate, avoid_subjects):
            continue
        bank_dir = data_dir / "media" / "ai_asset_bank" / candidate["bank_key"]
        has_ai_image = bool(
            candidate.pop("image_completed", False)
            and (bank_dir / "ai-image-vertical.jpg").is_file()
        )
        distinct_sources = {
            asset["source_url"] for asset in assets if asset.get("source_url")
        }
        score = (
            50
            + min(len(assets), 2) * 10
            + (5 if has_ai_image else 0)
            + (5 if distinct_sources else 0)
            + _CATEGORY_WEIGHT.get(candidate["category"], 5)
        )
        candidate.update({
            "ready_video_count": len(assets),
            "distinct_reference_count": max(1, len(distinct_sources)),
            "has_ai_image": has_ai_image,
            "source_url": next(iter(distinct_sources), ""),
            "selection_score": score,
            "production_ready": True,
        })
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            -int(item["selection_score"]),
            -int(item["ready_video_count"]),
            item["title_ko"],
        )
    )
    return candidates[:max(0, int(limit))]


def topic_matches_asset_candidate(topic: dict, candidates: Iterable[dict]) -> bool:
    allowed = {
        normalize_subject_key(str(item.get("exact_query") or ""))
        for item in candidates
        if item.get("exact_query")
    }
    identity = topic.get("visual_identity") or {}
    selected = {
        normalize_subject_key(str(query or "").removeprefix("exact:").strip())
        for query in identity.get("exact_queries") or []
    }
    return bool(allowed and selected and allowed & selected)
