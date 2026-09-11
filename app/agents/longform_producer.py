"""수동 검토용 6~10분 롱폼 렌더러."""
from __future__ import annotations

import hashlib
import json
import os
import re
import random
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.models import validate_longform_script
from app.services.ai_opening_library import AiOpeningLibrary, normalize_subject_key
from app.services.temp_cleanup import mark_temp_owner

from app.agents.story_producer import (
    _duration,
    _prepare_narration,
    _run_ffmpeg,
    _title_font,
    _tts_ssml,
    _tts_text,
)


LONGFORM_WIDTH = 1920
LONGFORM_HEIGHT = 1080
LONGFORM_OUTPUT = "output.mp4"
LONGFORM_STYLE_PRESETS = {
    "documentary": {
        "label": "다큐 집중형",
        "background": (11, 15, 20),
        "panel": (18, 26, 36),
        "accent": (238, 184, 69),
        "subtitle_font_size": 30,
    },
    "cinematic": {
        "label": "시네마틱 미스터리",
        "background": (7, 9, 14),
        "panel": (24, 20, 28),
        "accent": (164, 97, 255),
        "subtitle_font_size": 32,
    },
    "clean_news": {
        "label": "뉴스 해설형",
        "background": (18, 22, 28),
        "panel": (28, 34, 42),
        "accent": (74, 166, 255),
        "subtitle_font_size": 28,
    },
}


def _wrap_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars and len(lines) < max_lines - 1:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines[:max_lines]


def _create_scene_card(
    output: Path,
    *,
    title: str,
    chapter_title: str,
    role: str,
    index: int,
    total: int,
    style_id: str = "documentary",
) -> None:
    style = LONGFORM_STYLE_PRESETS.get(style_id, LONGFORM_STYLE_PRESETS["documentary"])
    image = Image.new("RGB", (LONGFORM_WIDTH, LONGFORM_HEIGHT), style["background"])
    draw = ImageDraw.Draw(image)
    for y in range(LONGFORM_HEIGHT):
        ratio = y / max(1, LONGFORM_HEIGHT - 1)
        color = (
            style["background"][0] + int(15 * ratio),
            style["background"][1] + int(25 * ratio),
            style["background"][2] + int(35 * ratio),
        )
        draw.line((0, y, LONGFORM_WIDTH, y), fill=color)

    accent = style["accent"]
    draw.rectangle((0, 0, LONGFORM_WIDTH, 96), fill=(0, 0, 0))
    draw.text(
        (80, 48),
        "이상한 지구기록",
        font=_title_font(36),
        fill=(230, 235, 240),
        anchor="lm",
    )
    draw.text(
        (LONGFORM_WIDTH - 80, 48),
        f"CHAPTER {index:02d}/{total:02d}",
        font=_title_font(30),
        fill=accent,
        anchor="rm",
    )

    draw.rounded_rectangle((110, 190, 1810, 890), radius=42, fill=style["panel"])
    draw.rectangle((110, 190, 125, 890), fill=accent)
    role_label = role.upper()
    draw.text((170, 255), role_label, font=_title_font(30), fill=accent)

    y = 350
    for line in _wrap_text(chapter_title, max_chars=24, max_lines=2):
        draw.text((170, y), line, font=_title_font(68), fill=(255, 255, 255))
        y += 86
    y += 26
    for line in _wrap_text(title, max_chars=34, max_lines=2):
        draw.text((174, y), line, font=_title_font(42), fill=(190, 200, 212))
        y += 56

    draw.text(
        (LONGFORM_WIDTH // 2, 1000),
        "실제 기록과 검증 가능한 단서를 따라갑니다",
        font=_title_font(28),
        fill=(145, 155, 168),
        anchor="mm",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, quality=92)


def _create_style_preview(
    output: Path,
    *,
    title: str,
    chapter_title: str,
    caption: str,
    style_id: str,
) -> dict:
    style = LONGFORM_STYLE_PRESETS.get(style_id, LONGFORM_STYLE_PRESETS["documentary"])
    _create_scene_card(
        output,
        title=title,
        chapter_title=chapter_title,
        role="hook",
        index=1,
        total=8,
        style_id=style_id,
    )
    with Image.open(output).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        subtitle_size = int(style["subtitle_font_size"])
        subtitle_font = _title_font(subtitle_size)
        caption_lines = _wrap_text(caption, max_chars=34, max_lines=2)
        box_height = 58 + 42 * len(caption_lines)
        y0 = LONGFORM_HEIGHT - 150 - box_height
        draw.rounded_rectangle(
            (300, y0, LONGFORM_WIDTH - 300, y0 + box_height),
            radius=26,
            fill=(0, 0, 0),
            outline=style["accent"],
            width=3,
        )
        text_y = y0 + 35
        for line in caption_lines:
            draw.text(
                (LONGFORM_WIDTH // 2, text_y),
                line,
                font=subtitle_font,
                fill=(255, 255, 255),
                anchor="ma",
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
            text_y += 42
        image.save(output, quality=92)
    return {
        "style_id": style_id,
        "label": style["label"],
        "preview_file": str(output.resolve()),
        "subtitle_font_size": subtitle_size,
    }


def _thumbnail_text(script: dict) -> tuple[str, str]:
    main = str(script.get("thumbnail_main") or "").strip()
    sub = str(script.get("thumbnail_sub") or "").strip()
    title = str(script.get("title") or "").strip()
    hook = str(script.get("hook") or "").strip()
    if not main:
        if "TOP" in title.upper():
            main = title[:18]
        else:
            main = "지구가 숨긴 TOP 5"
    if not sub:
        sub = "이건 진짜 이상함" if not hook else hook[:18].rstrip(" ,.")
    return main[:24], sub[:24]


def _thumbnail_main_lines(main_text: str) -> list[str]:
    words = str(main_text or "").strip().split()
    if not words:
        return ["지구의", "비밀"]
    upper_words = [word.upper() for word in words]
    if "TOP" in upper_words:
        index = upper_words.index("TOP")
        first = " ".join(words[:index]).strip()
        second = " ".join(words[index:]).strip()
        if first and second:
            return [first[:10], second[:10]]
    if len(words) == 1:
        text = words[0]
        if len(text) <= 4:
            return [text]
        split = max(2, len(text) // 2)
        return [text[:split], text[split:]]
    return [" ".join(words[:-1])[:10], words[-1][:10]]


def _cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    source_w, source_h = image.size
    scale = max(target_w / max(1, source_w), target_h / max(1, source_h))
    resized = image.resize(
        (int(source_w * scale), int(source_h * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _draw_distress(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], seed: str) -> None:
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    x0, y0, x1, y1 = box
    for _ in range(260):
        x = rng.randint(x0, max(x0, x1))
        y = rng.randint(y0, max(y0, y1))
        radius = rng.randint(1, 4)
        draw.ellipse((x, y, x + radius, y + radius), fill=(0, 0, 0, rng.randint(55, 130)))


def _draw_torn_strip(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int, int],
    seed: str,
) -> None:
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    x0, y0, x1, y1 = box
    points = []
    for x in range(x0, x1 + 1, 28):
        points.append((x, y0 + rng.randint(-14, 11)))
    for x in range(x1, x0 - 1, -28):
        points.append((x, y1 + rng.randint(-10, 16)))
    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=(95, 62, 15, 150), width=3)


def _draw_thumbnail_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
    stroke_width: int,
    seed: str,
) -> tuple[int, int, int, int]:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer, "RGBA")
    x, y = xy
    layer_draw.text(
        (x + 11, y + 13),
        text,
        font=font,
        fill=(0, 0, 0, 190),
        stroke_width=stroke_width + 3,
        stroke_fill=(0, 0, 0, 210),
    )
    layer_draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255),
    )
    bbox = layer_draw.textbbox(
        xy,
        text,
        font=font,
        stroke_width=stroke_width,
    )
    _draw_distress(layer_draw, bbox, seed)
    image.alpha_composite(layer)
    return bbox


def create_longform_thumbnail(script: dict, output: Path, background: Path | None = None) -> dict:
    """Create a high-contrast Korean YouTube thumbnail for a longform episode."""
    main_text, sub_text = _thumbnail_text(script)
    if background and Path(background).is_file():
        try:
            image = Image.open(background).convert("RGB")
            image = _cover_resize(image, (1280, 720))
            image = ImageEnhance.Contrast(image).enhance(1.55)
            image = ImageEnhance.Color(image).enhance(1.35)
            image = ImageEnhance.Sharpness(image).enhance(1.65)
        except Exception:
            image = Image.new("RGB", (1280, 720), (8, 10, 14))
    else:
        image = Image.new("RGB", (1280, 720), (8, 10, 14))
        base_draw = ImageDraw.Draw(image, "RGBA")
        for y in range(720):
            ratio = y / 719
            base_draw.line(
                (0, y, 1280, y),
                fill=(5 + int(20 * ratio), 10 + int(18 * ratio), 18 + int(35 * ratio), 255),
            )
        for x in range(640, 1280, 18):
            base_draw.line((x, 0, x - 260, 720), fill=(95, 0, 12, 38), width=10)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.4))
    image = image.convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(1280):
        ratio = x / 1279
        alpha = int(238 * (1.0 - ratio))
        draw.line((x, 0, x, 720), fill=(0, 0, 0, alpha))
    for y in range(720):
        ratio = y / 719
        draw.line((0, y, 1280, y), fill=(5, 8, 14, int(25 + 88 * ratio)))
    draw.polygon([(700, 0), (1280, 0), (1280, 720), (570, 720)], fill=(140, 0, 0, 75))
    draw.rectangle((0, 0, 810, 720), fill=(0, 0, 0, 38))
    main_lines = _thumbnail_main_lines(main_text)[:2]
    y = 66
    largest_bbox = (56, y, 760, y)
    for line_index, line in enumerate(main_lines):
        color = (252, 252, 248) if line_index == 0 else (238, 15, 15)
        compact_len = len(line.replace(" ", ""))
        if line_index == 0:
            font_size = 156 if compact_len <= 5 else 126
        else:
            font_size = 192 if compact_len <= 5 else 154
        font = _title_font(font_size)
        bbox = _draw_thumbnail_text(
            image,
            (58, y),
            line,
            font=font,
            fill=color,
            stroke_width=15,
            seed=f"{main_text}-{line_index}",
        )
        largest_bbox = (
            min(largest_bbox[0], bbox[0]),
            min(largest_bbox[1], bbox[1]),
            max(largest_bbox[2], bbox[2]),
            max(largest_bbox[3], bbox[3]),
        )
        y += int(font_size * 0.82)
    strip_y = min(598, max(414, y + 8))
    strip_x0 = 48
    strip_x1 = max(650, min(820, largest_bbox[2] + 70))
    _draw_torn_strip(
        draw,
        (strip_x0, strip_y, strip_x1, strip_y + 94),
        fill=(248, 201, 59, 248),
        seed=sub_text,
    )
    draw.rectangle((strip_x0 + 12, strip_y + 10, strip_x1 - 14, strip_y + 84), fill=(255, 210, 70, 24))
    draw.text(
        (92, strip_y + 47),
        sub_text,
        font=_title_font(58),
        fill=(0, 0, 0),
        anchor="lm",
        stroke_width=2,
        stroke_fill=(255, 232, 135),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, quality=94)
    return {
        "thumbnail_file": str(output.resolve()),
        "main_text": main_text,
        "sub_text": sub_text,
        "style_id": "approved_reference_poster_v2",
        "layout": "left_big_white_red_yellow_torn_strip",
        "background_mode": "ai_or_stock_poster",
    }


def generate_longform_style_previews(
    output_dir: Path,
    *,
    title: str,
    chapter_title: str,
    caption: str,
) -> dict:
    """Create local PNG previews so the operator can approve longform styling first."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    styles = [
        _create_style_preview(
            directory / f"{style_id}.png",
            title=title,
            chapter_title=chapter_title,
            caption=caption,
            style_id=style_id,
        )
        for style_id in ("documentary", "cinematic", "clean_news")
    ]
    manifest = {"created_at": datetime.now().isoformat(), "styles": styles}
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _write_longform_srt(
    script: dict,
    scene_starts: dict[int, float],
    audio_durations: dict[int, float],
    output: Path,
) -> None:
    lines = []
    cue = 0
    for scene in script.get("scenes", []):
        text = str(scene["narration"])
        chunks = _longform_subtitle_chunks(text)
        if not chunks:
            chunks = [text]
        cursor = scene_starts[scene["n"]]
        duration = max(0.1, audio_durations[scene["n"]])
        weights = [max(1, len(chunk.replace("\n", ""))) for chunk in chunks]
        total_weight = sum(weights)
        for chunk, weight in zip(chunks, weights):
            chunk_duration = duration * weight / total_weight
            cue += 1
            lines.extend(
                [
                    str(cue),
                    f"{_srt_time(cursor)} --> {_srt_time(cursor + chunk_duration)}",
                    chunk,
                    "",
                ]
            )
            cursor += chunk_duration
    output.write_text("\n".join(lines), encoding="utf-8")


def _longform_subtitle_chunks(text: str) -> list[str]:
    """Split narration into safe one/two-line subtitle cues for 16:9 longform."""
    sentence_chunks = re.split(r"(?<=[.!?…。！？])\s+", text.strip())
    result: list[str] = []
    for sentence in [chunk.strip() for chunk in sentence_chunks if chunk.strip()]:
        wrapped = textwrap.wrap(
            sentence,
            width=34,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not wrapped:
            continue
        safe_lines: list[str] = []
        for line in wrapped:
            if len(line) <= 34:
                safe_lines.append(line)
                continue
            safe_lines.extend(
                line[index : index + 34] for index in range(0, len(line), 34)
            )
        for index in range(0, len(safe_lines), 2):
            result.append("\n".join(safe_lines[index : index + 2]))
    return result


def _srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds % 1) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _encode_longform_card(
    image: Path,
    narration: Path,
    output: Path,
    duration: float,
    ffmpeg_path: str,
    *,
    motion_index: int,
) -> None:
    vf = _longform_still_filter()
    _run_ffmpeg(
        [
            ffmpeg_path,
            "-loop",
            "1",
            "-i",
            str(image),
            "-i",
            str(narration),
            "-vf",
            vf,
            "-t",
            f"{duration:.3f}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        timeout=900,
    )


def _longform_still_filter() -> str:
    """Build FFmpeg filter that keeps longform still images visually stable."""
    return (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        "fps=30,setsar=1,format=yuv420p"
    )


def _longform_video_filter() -> str:
    """Build FFmpeg filter for true 16:9 longform sources without Shorts sidebars."""
    return (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,setsar=1,format=yuv420p"
    )


def _longform_playback_tempo() -> float:
    """Return the pitch-preserving longform narration tempo."""
    try:
        tempo = float(os.getenv("LONGFORM_TTS_SPEED", "1.2"))
    except ValueError:
        return 1.2
    return tempo if 0.8 <= tempo <= 1.25 else 1.2


def _longform_scene_duration(planned_duration: float, audio_duration: float) -> float:
    """Match scene length to narration so longform never drifts into dead air."""
    audio = max(0.1, float(audio_duration or 0))
    return round(audio, 3)


def _should_render_longform_scene(
    cursor: float,
    max_total_duration: float | None,
    transition_duration: float,
) -> bool:
    if max_total_duration is None:
        return True
    remaining = float(max_total_duration) - float(cursor)
    return remaining > max(0.8, float(transition_duration) + 0.25)


def _media_asset_for_scene(media_board: dict, scene_number: int) -> dict | None:
    """Return the first materialized asset for a scene from media_board.json."""
    for board_scene in media_board.get("scenes") or []:
        if int(board_scene.get("n") or 0) != int(scene_number):
            continue
        assets = board_scene.get("assets") or []
        for asset in assets:
            local_path = str(asset.get("local_path") or asset.get("path") or "").strip()
            if local_path and Path(local_path).is_file():
                return asset
    return None


def _media_asset_is_video(asset: dict | None) -> bool:
    if not isinstance(asset, dict):
        return False
    media_type = str(asset.get("media_type") or asset.get("type") or "").lower()
    provider = str(asset.get("provider") or "").lower()
    local_path = str(asset.get("local_path") or asset.get("path") or "").strip()
    suffix = Path(local_path).suffix.lower() if local_path else ""
    return (
        media_type == "video"
        or suffix in {".mp4", ".mov", ".webm", ".mkv"}
        or provider
        in {
            "pexels_video",
            "pixabay_video",
            "veo",
            "vertex_veo",
            "veo-3.1-fast-generate-001",
        }
    )


def _media_asset_is_portrait(asset: dict | None) -> bool:
    if not isinstance(asset, dict):
        return False
    try:
        width = int(asset.get("width") or 0)
        height = int(asset.get("height") or 0)
    except (TypeError, ValueError):
        return False
    return width > 0 and height > 0 and height > width


def _assert_preview_has_video_sources(script: dict, media_board: dict) -> None:
    """Prevent review previews that are just still images with narration."""
    if not media_board:
        raise ValueError("롱폼 30초 미리보기에는 영상 소스가 포함된 media_board.json이 필요합니다.")
    cursor = 0.0
    checked = 0
    for scene in script.get("scenes") or []:
        if cursor >= 30.0:
            break
        checked += 1
        asset = _media_asset_for_scene(media_board, int(scene["n"]))
        if not _media_asset_is_video(asset):
            raise ValueError(
                f"롱폼 30초 미리보기 장면 {scene['n']}에 영상 소스가 없습니다. "
                "정지 이미지/카드 fallback 미리보기는 생성하지 않습니다."
            )
        if _media_asset_is_portrait(asset):
            raise ValueError(f"롱폼 30초 미리보기 장면 {scene['n']}에 세로 영상 소스가 포함됨")
        cursor += float(scene.get("duration_sec") or 0)
    if checked == 0:
        raise ValueError("롱폼 30초 미리보기에 사용할 장면이 없습니다.")


def _strict_video_sources_enabled() -> bool:
    return str(os.getenv("LONGFORM_REQUIRE_VIDEO_SOURCES", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _required_longform_video_sources(script: dict) -> int:
    scenes = script.get("scenes") or []
    return 30 if len(scenes) >= 40 else min(30, len(scenes))


def _assert_final_media_mix(script: dict, media_board: dict) -> None:
    """Prevent final longform renders from falling back to cards or weak video counts."""
    if not media_board:
        raise ValueError("롱폼 최종 렌더링에는 media_board.json이 필요합니다.")
    video_count = 0
    missing_scenes = []
    portrait_scenes = []
    for scene in script.get("scenes") or []:
        asset = _media_asset_for_scene(media_board, int(scene["n"]))
        if asset is None:
            missing_scenes.append(str(scene["n"]))
            continue
        if _media_asset_is_video(asset):
            video_count += 1
            if _media_asset_is_portrait(asset):
                portrait_scenes.append(str(scene["n"]))
    if missing_scenes:
        raise ValueError(
            "롱폼 최종 렌더링에 사용할 수 있는 미디어가 없는 장면: "
            + ", ".join(missing_scenes[:10])
        )
    if portrait_scenes:
        raise ValueError("롱폼 최종 렌더링에 세로 영상 소스가 포함됨: " + ", ".join(portrait_scenes[:10]))
    required = _required_longform_video_sources(script)
    if video_count < required:
        raise ValueError(f"롱폼 영상 소스 {video_count}개 — 최소 {required}개 필요")


def _thumbnail_background_from_board(media_board: dict) -> Path | None:
    for board_scene in media_board.get("scenes") or []:
        for asset in board_scene.get("assets") or []:
            media_type = str(asset.get("media_type") or asset.get("type") or "").lower()
            local_path = str(asset.get("local_path") or asset.get("path") or "").strip()
            if media_type in {"image", "photo"} and local_path and Path(local_path).is_file():
                return Path(local_path)
    return None


def _extract_video_thumbnail_frame(
    media: Path,
    output: Path,
    ffmpeg_path: str,
) -> Path | None:
    if not media.is_file():
        return None
    try:
        _run_ffmpeg(
            [
                ffmpeg_path,
                "-y",
                "-ss",
                "00:00:01",
                "-i",
                str(media),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output),
            ],
            timeout=120,
        )
    except Exception:
        return None
    return output if output.is_file() else None


def _thumbnail_background_from_media_board(
    media_board: dict,
    ffmpeg_path: str,
    tmp_path: Path,
) -> Path | None:
    first_image: Path | None = None
    for board_scene in media_board.get("scenes") or []:
        for asset in board_scene.get("assets") or []:
            media_type = str(asset.get("media_type") or asset.get("type") or "").lower()
            provider = str(asset.get("provider") or "").lower()
            local_path = str(asset.get("local_path") or asset.get("path") or "").strip()
            if not local_path:
                continue
            path = Path(local_path)
            if not path.is_file():
                continue
            is_video = media_type == "video" or provider in {
                "pexels_video",
                "pixabay_video",
                "veo",
                "vertex_veo",
                "veo-3.1-fast-generate-001",
            }
            if is_video:
                if _media_asset_is_portrait(asset):
                    continue
                return _extract_video_thumbnail_frame(
                    path,
                    tmp_path / "thumbnail-background.jpg",
                    ffmpeg_path,
                )
            if first_image is None and media_type in {"image", "photo"}:
                first_image = path
    return first_image


def _encode_longform_media(
    media: Path,
    narration: Path,
    output: Path,
    duration: float,
    ffmpeg_path: str,
    *,
    motion_index: int,
) -> None:
    """Encode a materialized longform media asset with narration audio."""
    is_image = media.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    if is_image:
        _encode_longform_card(
            media,
            narration,
            output,
            duration,
            ffmpeg_path,
            motion_index=motion_index,
        )
        return
    vf = _longform_video_filter()
    _run_ffmpeg(
        [
            ffmpeg_path,
            "-stream_loop",
            "-1",
            "-i",
            str(media),
            "-i",
            str(narration),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-t",
            f"{duration:.3f}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        timeout=900,
    )


def _finish_longform(
    concat_video: Path,
    output: Path,
    srt_path: Path,
    ffmpeg_path: str,
    tmp_path: Path,
    *,
    planned_duration: float,
) -> None:
    import os

    font = os.getenv("SUBTITLE_FONT", "NanumGothic")
    style = _longform_subtitle_style(font, "clean_news")
    _run_ffmpeg(
        [
            ffmpeg_path,
            "-i",
            str(concat_video),
            "-t",
            f"{planned_duration:.3f}",
            "-vf",
            f"subtitles=longform.srt:force_style='{style}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-y",
            str(output.resolve()),
        ],
        cwd=tmp_path,
        timeout=1800,
    )


def _longform_subtitle_style(font: str, style_id: str = "clean_news") -> str:
    """Return ASS style for readable longform subtitles, not Shorts captions."""
    presets = {
        "clean_news": {
            "font_size": 15,
            "outline": 1,
            "shadow": 0,
            "margin_v": 36,
            "back": "&H80000000",
        },
        "documentary": {
            "font_size": 15,
            "outline": 1,
            "shadow": 0,
            "margin_v": 82,
            "back": "&H80000000",
        },
        "cinematic": {
            "font_size": 15,
            "outline": 1,
            "shadow": 1,
            "margin_v": 78,
            "back": "&HAA000000",
        },
    }
    preset = presets.get(style_id, presets["clean_news"])
    return (
        f"FontName={font},FontSize={preset['font_size']},Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        f"BackColour={preset['back']},BorderStyle=4,"
        f"Outline={preset['outline']},Shadow={preset['shadow']},"
        f"Alignment=2,MarginL=320,MarginR=320,MarginV={preset['margin_v']},"
        "WrapStyle=2"
    )


def _concat_longform_files(
    files: list[Path],
    output: Path,
    ffmpeg_path: str,
    tmp_path: Path,
    *,
    durations: list[float] | None = None,
    transition_duration: float | None = None,
) -> None:
    transition = (
        _longform_transition_duration()
        if transition_duration is None
        else max(0.0, float(transition_duration))
    )
    if len(files) > 1 and transition > 0 and len(files) <= 8:
        _concat_longform_files_with_crossfade(
            files,
            output,
            ffmpeg_path,
            durations=durations,
            transition_duration=transition,
        )
        return

    manifest = tmp_path / f"{output.stem}-concat.txt"
    lines = [
        f"file '{path.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
        for path in files
    ]
    manifest.write_text("\n".join(lines), encoding="utf-8")
    _run_ffmpeg(
        [
            ffmpeg_path,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-fflags",
            "+genpts",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        timeout=1800,
    )


def _longform_transition_duration() -> float:
    try:
        configured = float(os.getenv("LONGFORM_SCENE_TRANSITION_SEC", "0.28"))
    except ValueError:
        return 0.28
    return min(0.5, max(0.0, configured))


def _concat_longform_files_with_crossfade(
    files: list[Path],
    output: Path,
    ffmpeg_path: str,
    *,
    durations: list[float] | None,
    transition_duration: float,
) -> None:
    durations = durations or [0.0 for _ in files]
    if len(durations) != len(files):
        raise ValueError("롱폼 장면 파일 수와 duration 수가 다릅니다.")

    command = [ffmpeg_path]
    for path in files:
        command.extend(["-i", str(path)])

    filters: list[str] = []
    for index in range(len(files)):
        filters.append(
            f"[{index}:v]setpts=PTS-STARTPTS,fps=30,format=yuv420p[v{index}]"
        )
        filters.append(f"[{index}:a]asetpts=PTS-STARTPTS[a{index}]")

    current_v = "v0"
    current_a = "a0"
    elapsed = float(durations[0])
    for index in range(1, len(files)):
        offset = max(0.01, elapsed - transition_duration)
        next_v = "vout" if index == len(files) - 1 else f"vx{index}"
        next_a = "aout" if index == len(files) - 1 else f"ax{index}"
        filters.append(
            f"[{current_v}][v{index}]"
            f"xfade=transition=fade:duration={transition_duration:.3f}:offset={offset:.3f}"
            f"[{next_v}]"
        )
        filters.append(
            f"[{current_a}][a{index}]"
            f"acrossfade=d={transition_duration:.3f}:c1=tri:c2=tri"
            f"[{next_a}]"
        )
        current_v = next_v
        current_a = next_a
        elapsed = elapsed + float(durations[index]) - transition_duration

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ]
    )
    _run_ffmpeg(command, timeout=1800)


def _longform_click_package(script: dict) -> dict:
    title = str(script.get("title") or "").strip()
    hook = str(script.get("hook") or "").strip()
    tags = [str(tag).strip() for tag in script.get("tags") or [] if str(tag).strip()]
    return {
        "opening_question": hook,
        "thumbnail_brief": (
            f"어두운 실제 지형/기록 이미지 위에 '{title}'를 크게 배치하고, "
            "노란색 단서 표시와 지도 핀으로 미스터리 다큐 느낌을 강조"
        ),
        "title_candidates": [
            title,
            f"{title}, 왜 아직도 설명이 어려울까?",
            f"지도에 남은 이상한 기록: {title}",
        ][:3],
        "tags": tags[:8],
    }


def _reusable_ai_assets(script: dict, data_dir: Path, run_id: str) -> list[dict]:
    identity = script.get("visual_identity") or {}
    if not identity.get("required_exact"):
        return []
    queries = identity.get("exact_queries") or []
    if not queries:
        return []
    subject = str(queries[0]).removeprefix("exact:").strip()
    subject_key = normalize_subject_key(subject)
    asset = AiOpeningLibrary(data_dir).find_reusable_asset(subject_key)
    if asset is None:
        return []
    AiOpeningLibrary(data_dir).mark_asset_used(asset.asset_id, run_id)
    return [
        {
            "asset_id": asset.asset_id,
            "subject_key": asset.subject_key,
            "path": str(asset.opening_path),
            "source_url": asset.source_url,
            "model": asset.model,
            "reused": True,
            "reusable_for_shorts": True,
        }
    ]


def _render_longform(
    data_dir: Path,
    run_id: str,
    ffmpeg_path: str,
    *,
    output_name: str,
    log_name: str,
    max_total_duration: float | None = None,
) -> dict:
    """Render a full longform video or a bounded preview."""
    data_dir = Path(data_dir)
    work_dir = data_dir / "longform" / run_id
    script_file = work_dir / "script.json"
    if not script_file.is_file():
        raise FileNotFoundError(f"longform script.json이 없습니다: {script_file}")
    script = validate_longform_script(
        json.loads(script_file.read_text(encoding="utf-8"))
    )
    media_board_file = work_dir / "media_board.json"
    media_board = (
        json.loads(media_board_file.read_text(encoding="utf-8"))
        if media_board_file.is_file()
        else {}
    )
    if max_total_duration is not None:
        _assert_preview_has_video_sources(script, media_board)
    elif _strict_video_sources_enabled():
        _assert_final_media_mix(script, media_board)

    with tempfile.TemporaryDirectory(prefix="shorts-factory-longform-") as tmpdir:
        tmp_path = Path(tmpdir)
        mark_temp_owner(tmp_path)
        tts_results = []
        scene_videos = []
        media_sources = []
        rendered_scenes = []
        scene_starts = {}
        audio_durations = {}
        cursor = 0.0
        tempo = _longform_playback_tempo()
        transition = _longform_transition_duration()
        for index, scene in enumerate(script["scenes"], start=1):
            if not _should_render_longform_scene(cursor, max_total_duration, transition):
                break
            raw = tmp_path / f"narration-{index:02d}.mp3"
            wav = tmp_path / f"narration-{index:02d}.wav"
            result, duration = _prepare_narration(
                _tts_text(scene["narration"]),
                raw,
                wav,
                ffmpeg_path,
                ssml=_tts_ssml(scene["narration"]),
            )
            if tempo != 1.0:
                from app.agents.story_producer import _retime_audio

                _retime_audio(wav, tempo, ffmpeg_path)
                duration = _duration(wav, ffmpeg_path)
            tts_results.append(result)
            scene_duration = _longform_scene_duration(
                float(scene["duration_sec"]),
                duration,
            )
            if max_total_duration is not None:
                remaining = max_total_duration - cursor
                if remaining <= 0:
                    break
                scene_duration = min(scene_duration, remaining)
            audio_durations[scene["n"]] = min(duration, scene_duration)
            scene_start = max(0.0, cursor - (transition if index > 1 else 0.0))
            scene_starts[scene["n"]] = scene_start
            cursor = scene_start + scene_duration
            rendered_scene = dict(scene)
            rendered_scene["duration_sec"] = scene_duration
            rendered_scenes.append(rendered_scene)

            scene_video = tmp_path / f"scene-{index:02d}.mp4"
            asset = _media_asset_for_scene(media_board, scene["n"]) if media_board else None
            if asset is not None:
                media_path = Path(str(asset.get("local_path") or asset.get("path")))
                _encode_longform_media(
                    media_path,
                    wav,
                    scene_video,
                    scene_duration,
                    ffmpeg_path,
                    motion_index=index,
                )
                media_sources.append(
                    {
                        "scene": scene["n"],
                        "local_path": media_path.as_posix(),
                        "tier": asset.get("tier"),
                        "provider": asset.get("provider"),
                        "source_url": asset.get("source_url"),
                    }
                )
            else:
                card = tmp_path / f"card-{index:02d}.jpg"
                _create_scene_card(
                    card,
                    title=script["title"],
                    chapter_title=scene["chapter_title"],
                    role=scene["role"],
                    index=index,
                    total=len(script["scenes"]),
                    style_id=str(script.get("style_id") or "clean_news"),
                )
                _encode_longform_card(
                    card,
                    wav,
                    scene_video,
                    scene_duration,
                    ffmpeg_path,
                    motion_index=index,
                )
            scene_videos.append(scene_video)

        concat_video = tmp_path / "longform-concat.mp4"
        scene_durations = [
            float(scene.get("duration_sec") or 0) for scene in rendered_scenes
        ]
        _concat_longform_files(
            scene_videos,
            concat_video,
            ffmpeg_path,
            tmp_path,
            durations=scene_durations,
            transition_duration=transition,
        )
        srt_path = tmp_path / "longform.srt"
        render_script = dict(script)
        render_script["scenes"] = rendered_scenes
        _write_longform_srt(render_script, scene_starts, audio_durations, srt_path)
        output_mp4 = work_dir / output_name
        _finish_longform(
            concat_video,
            output_mp4,
            srt_path,
            ffmpeg_path,
            tmp_path,
            planned_duration=cursor,
        )
        actual_duration = _duration(output_mp4, ffmpeg_path)
        thumbnail_background = (
            _thumbnail_background_from_media_board(media_board, ffmpeg_path, tmp_path)
            if media_board
            else None
        )
        thumbnail = create_longform_thumbnail(
            script,
            work_dir / "thumbnail.png",
            background=thumbnail_background,
        )

    ai_assets = _reusable_ai_assets(script, data_dir, run_id)
    produce_log = {
        "date": run_id,
        "timestamp": datetime.now().isoformat(),
        "format": "longform",
        "output_file": str(output_mp4.resolve()),
        "thumbnail_file": thumbnail["thumbnail_file"],
        "preview": max_total_duration is not None,
        "planned_duration": script["total_duration_sec"],
        "actual_duration": round(actual_duration, 1),
        "script_sha256": hashlib.sha256(script_file.read_bytes()).hexdigest(),
        "click_package": _longform_click_package(script),
        "thumbnail": thumbnail,
        "chapter_titles": [
            scene["chapter_title"] for scene in rendered_scenes
        ],
        "tts": {
            "provider": tts_results[0].provider if tts_results else "",
            "voice": tts_results[0].voice if tts_results else "",
            "speed": tempo,
        },
        "scene_starts": scene_starts,
        "audio_durations": audio_durations,
        "ai_assets": ai_assets,
        "ai_reuse_policy": "ready exact-subject AI assets are reusable for later Shorts",
        "media_board_used": bool(media_board),
        "media_quality_gate": media_board.get("gate", {}) if media_board else {},
        "media_sources": media_sources,
    }
    (work_dir / log_name).write_text(
        json.dumps(produce_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return produce_log


def run_longform_producer(data_dir: Path, run_id: str, ffmpeg_path: str) -> dict:
    """Render `data/longform/{run_id}/script.json` into a reviewable MP4."""
    return _render_longform(
        data_dir,
        run_id,
        ffmpeg_path,
        output_name=LONGFORM_OUTPUT,
        log_name="produce_log.json",
    )


def run_longform_preview(data_dir: Path, run_id: str, ffmpeg_path: str) -> dict:
    """Render the opening two minutes into `preview_30s.mp4` for review."""
    return _render_longform(
        data_dir,
        run_id,
        ffmpeg_path,
        output_name="preview_30s.mp4",
        log_name="preview_log.json",
        max_total_duration=120.0,
    )
