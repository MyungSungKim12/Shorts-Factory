"""롱폼 제작 전 장면별 미디어 확보 가능성을 점검한다."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.ai_opening_library import AiOpeningLibrary, normalize_subject_key
from app.services.longform_media_board import longform_media_gate, media_tier_for_source
from app.services.media_library import (
    MediaCandidate,
    _download_candidate,
    _is_usable_download,
    _nasa_image_candidates,
    _pexels_photo_candidates,
    _pexels_video_candidates,
    _pixabay_video_candidates,
    _wikimedia_image_candidates,
    exact_candidate_matches,
    stock_candidate_matches,
)


MEDIA_BOARD_FILE = "media_board.json"
CONTACT_SHEET_FILE = "media_contact_sheet.png"
TIER_PRIORITY = {"A": 0, "C": 1, "B": 2, "D": 3}


def _scene_query(scene: dict, script: dict) -> str:
    query = str(scene.get("visual_query") or "").strip()
    if query:
        return query
    identity = script.get("visual_identity") or {}
    exact_queries = identity.get("exact_queries") or []
    if exact_queries:
        return str(exact_queries[0] or "").strip()
    return str(scene.get("chapter_title") or script.get("title") or "").strip()


def _candidate_to_source(
    candidate: MediaCandidate,
    *,
    query: str,
    exact: bool = False,
    strong: bool = False,
) -> dict:
    source = {
        "provider": candidate.provider,
        "media_id": candidate.media_id,
        "source_url": candidate.source_url,
        "download_url": candidate.download_url,
        "width": candidate.width,
        "height": candidate.height,
        "media_type": candidate.media_type,
        "keyword": query,
        "license": candidate.license,
        "attribution": candidate.attribution,
        "description": candidate.description,
        "exact_match": exact,
        "strong_match": strong,
    }
    source["tier"] = media_tier_for_source(source)
    return source


def _source_to_candidate(source: dict) -> MediaCandidate:
    return MediaCandidate(
        provider=str(source.get("provider") or ""),
        media_id=str(source.get("media_id") or source.get("asset_id") or ""),
        source_url=str(source.get("source_url") or ""),
        download_url=str(source.get("download_url") or source.get("path") or ""),
        width=int(source.get("width") or 0),
        height=int(source.get("height") or 0),
        media_type=str(source.get("media_type") or "image"),
        keyword=str(source.get("keyword") or ""),
        license=str(source.get("license") or ""),
        attribution=str(source.get("attribution") or ""),
        description=str(source.get("description") or source.get("subject_evidence") or ""),
        alternate_download_url=str(source.get("alternate_download_url") or ""),
    )


def _asset_to_source(asset, *, query: str) -> dict:
    source_url = str(getattr(asset, "source_url", "") or "")
    source = {
        "provider": str(getattr(asset, "model", "") or "ai_asset"),
        "asset_id": str(getattr(asset, "asset_id", "") or ""),
        "subject_key": str(getattr(asset, "subject_key", "") or ""),
        "source_url": source_url,
        "source_reference": source_url,
        "path": str(getattr(asset, "opening_path", "") or ""),
        "media_type": "video",
        "keyword": query,
        "exact_match": True,
        "strong_match": True,
        "reused": True,
        "reusable_for_shorts": True,
    }
    source["tier"] = media_tier_for_source(source)
    return source


def _select_candidates(query: str, *, exact: bool) -> list[dict]:
    selected: list[dict] = []
    provider_query = query.split(":", 1)[1].strip() if exact and ":" in query else query

    for candidate in _wikimedia_image_candidates(provider_query):
        matches = exact_candidate_matches(provider_query, candidate) if exact else True
        if matches:
            selected.append(
                _candidate_to_source(candidate, query=provider_query, exact=matches, strong=True)
            )
            break

    for candidate in _nasa_image_candidates(provider_query):
        selected.append(
            _candidate_to_source(candidate, query=provider_query, exact=exact, strong=True)
        )
        break

    for collector in (
        _pexels_video_candidates,
        _pixabay_video_candidates,
        _pexels_photo_candidates,
    ):
        for candidate in collector(provider_query):
            if stock_candidate_matches(provider_query, candidate):
                selected.append(
                    _candidate_to_source(
                        candidate, query=provider_query, exact=False, strong=True
                    )
                )
                break
        if any(item["provider"] in {"pexels_video", "pixabay_video", "pexels_image"} for item in selected):
            break

    return selected[:3]


def _select_reusable_ai(data_dir: Path, query: str) -> list[dict]:
    subject = query.removeprefix("exact:").strip()
    if not subject:
        return []
    asset = AiOpeningLibrary(data_dir).find_reusable_asset(normalize_subject_key(subject))
    if asset is None:
        return []
    return [_asset_to_source(asset, query=query)]


def _font(size: int):
    for path in (
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _short(value: str, limit: int = 92) -> str:
    value = str(value or "").replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _write_contact_sheet(board: dict, output: Path) -> None:
    scenes = board.get("scenes") or []
    width = 1600
    row_h = 150
    height = 190 + max(1, len(scenes)) * row_h
    image = Image.new("RGB", (width, height), (18, 22, 28))
    draw = ImageDraw.Draw(image)
    title_font = _font(34)
    body_font = _font(24)
    small_font = _font(20)
    draw.text((40, 34), "롱폼 미디어 보드", font=title_font, fill=(255, 255, 255))
    draw.text(
        (40, 84),
        _short(f"{board.get('run_id', '')} · {board.get('title', '')}", 110),
        font=body_font,
        fill=(190, 205, 220),
    )
    gate = board.get("gate") or {}
    gate_color = (76, 217, 100) if gate.get("passed") else (255, 149, 0)
    draw.text(
        (40, 124),
        f"Gate: {'PASS' if gate.get('passed') else 'REVIEW'} · quality {gate.get('quality_runtime_ratio', 0):.0%}",
        font=small_font,
        fill=gate_color,
    )

    y = 175
    for scene in scenes:
        assets = scene.get("assets") or []
        best = assets[0] if assets else {}
        tier = str(best.get("tier") or "D")
        tier_color = {
            "A": (80, 220, 120),
            "B": (105, 180, 255),
            "C": (190, 120, 255),
            "D": (255, 170, 80),
        }.get(tier, (255, 170, 80))
        draw.rounded_rectangle((32, y, width - 32, y + row_h - 18), radius=18, fill=(28, 34, 42))
        draw.text((56, y + 24), f"{scene.get('n', 0):02d}", font=title_font, fill=tier_color)
        draw.text(
            (130, y + 22),
            _short(f"{scene.get('role', '')} · {scene.get('chapter_title', '')}", 72),
            font=body_font,
            fill=(245, 248, 252),
        )
        draw.text(
            (130, y + 62),
            _short(f"query: {scene.get('query', '')}", 100),
            font=small_font,
            fill=(160, 172, 188),
        )
        draw.text(
            (130, y + 96),
            _short(f"Tier {tier} · {best.get('provider', 'none')} · {best.get('source_url', '')}", 118),
            font=small_font,
            fill=(205, 214, 224),
        )
        y += row_h

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, quality=92)


def prepare_longform_media_board(data_dir: Path, run_id: str) -> dict:
    """Write a reviewable media board for a longform run without rendering video."""
    data_dir = Path(data_dir)
    work_dir = data_dir / "longform" / run_id
    script_file = work_dir / "script.json"
    if not script_file.is_file():
        raise FileNotFoundError(f"longform script.json이 없습니다: {script_file}")

    script = json.loads(script_file.read_text(encoding="utf-8"))
    board_scenes = []
    for scene in script.get("scenes") or []:
        query = _scene_query(scene, script)
        exact = query.lower().startswith("exact:")
        assets = [
            *_select_reusable_ai(data_dir, query),
            *_select_candidates(query, exact=exact),
        ]
        for asset in assets:
            asset["duration_sec"] = float(scene.get("duration_sec") or 0)
        board_scenes.append(
            {
                "n": int(scene.get("n") or len(board_scenes) + 1),
                "role": str(scene.get("role") or ""),
                "chapter_title": str(scene.get("chapter_title") or ""),
                "duration_sec": float(scene.get("duration_sec") or 0),
                "query": query,
                "assets": assets,
            }
        )

    board = {
        "run_id": run_id,
        "title": str(script.get("title") or ""),
        "created_at": datetime.now().isoformat(),
        "workflow": "asset_first_longform",
        "scenes": board_scenes,
    }
    board["gate"] = longform_media_gate(board)

    work_dir.mkdir(parents=True, exist_ok=True)
    board_file = work_dir / MEDIA_BOARD_FILE
    board_file.write_text(
        json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_contact_sheet(board, work_dir / CONTACT_SHEET_FILE)
    return board


def _asset_sort_key(asset: dict) -> tuple[int, int]:
    tier = str(asset.get("tier") or media_tier_for_source(asset)).upper()
    is_video = 0 if str(asset.get("media_type") or "").lower() == "video" else 1
    return TIER_PRIORITY.get(tier, 9), is_video


def _local_suffix(asset: dict) -> str:
    media_type = str(asset.get("media_type") or "").lower()
    if media_type == "video":
        return ".mp4"
    provider = str(asset.get("provider") or "").lower()
    if provider in {"wikimedia_image", "nasa_image", "pexels_image"}:
        return ".jpg"
    return ".jpg"


def materialize_longform_media_board(data_dir: Path, run_id: str) -> dict:
    """Download selected media-board assets into the longform run folder."""
    data_dir = Path(data_dir)
    work_dir = data_dir / "longform" / run_id
    board_file = work_dir / MEDIA_BOARD_FILE
    if not board_file.is_file():
        raise FileNotFoundError(f"media_board.json이 없습니다: {board_file}")

    board = json.loads(board_file.read_text(encoding="utf-8"))
    media_dir = work_dir / "media"
    for scene in board.get("scenes") or []:
        assets = scene.get("assets") if isinstance(scene, dict) else []
        if not isinstance(assets, list):
            continue
        selected = sorted(assets, key=_asset_sort_key)
        for index, asset in enumerate(selected, start=1):
            existing = str(asset.get("path") or "")
            if existing and Path(existing).is_file():
                asset["local_path"] = existing
                asset["materialized"] = True
                break
            download_url = str(asset.get("download_url") or "").strip()
            if not download_url:
                continue
            suffix = _local_suffix(asset)
            output = media_dir / f"scene-{int(scene.get('n') or 0):02d}-{index:02d}{suffix}"
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                downloaded = _download_candidate(_source_to_candidate(asset), output)
            except Exception as exc:
                asset["materialize_error"] = str(exc)
                continue
            if not _is_usable_download(output):
                output.unlink(missing_ok=True)
                asset["materialize_error"] = "downloaded media is not usable"
                continue
            asset["local_path"] = output.as_posix()
            asset["download_bytes"] = int(downloaded)
            asset["materialized"] = True
            break
    board["materialized_at"] = datetime.now().isoformat()
    board_file.write_text(
        json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_contact_sheet(board, work_dir / CONTACT_SHEET_FILE)
    return board
