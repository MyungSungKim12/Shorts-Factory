"""수동 검토용 5~10분 롱폼 렌더러."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models import validate_longform_script
from app.services.ai_opening_library import AiOpeningLibrary, normalize_subject_key
from app.services.temp_cleanup import mark_temp_owner

from app.agents.story_producer import (
    _duration,
    _prepare_narration,
    _run_ffmpeg,
    _subtitle_style,
    _title_font,
    _tts_text,
    story_playback_tempo,
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
        chunks = re.split(r"(?<=[.!?…])\s+", text.strip())
        chunks = [chunk for chunk in chunks if chunk]
        if not chunks:
            chunks = [text]
        cursor = scene_starts[scene["n"]]
        duration = max(0.1, audio_durations[scene["n"]])
        weights = [max(1, len(chunk)) for chunk in chunks]
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
    frames = max(1, round(duration * 30))
    direction = motion_index % 3
    if direction == 1:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "(ih-ih/zoom)*(on/max(1,d-1))"
    elif direction == 2:
        x_expr = "(iw-iw/zoom)*(on/max(1,d-1))"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    vf = (
        "scale=2048:1152:force_original_aspect_ratio=increase,"
        "crop=2048:1152,"
        "zoompan=z='min(zoom+0.00025,1.04)':"
        f"x='{x_expr}':y='{y_expr}':d={frames}:s=1920x1080:fps=30,"
        "setsar=1,format=yuv420p"
    )
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
) -> None:
    import os

    font = os.getenv("SUBTITLE_FONT", "Malgun Gothic")
    style = _subtitle_style(font).replace("FontSize=16", "FontSize=24")
    style = style.replace("MarginV=90", "MarginV=60")
    _run_ffmpeg(
        [
            ffmpeg_path,
            "-i",
            str(concat_video),
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


def _concat_longform_files(
    files: list[Path], output: Path, ffmpeg_path: str, tmp_path: Path
) -> None:
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
            "-c",
            "copy",
            "-y",
            str(output),
        ],
        timeout=900,
    )


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


def run_longform_producer(data_dir: Path, run_id: str, ffmpeg_path: str) -> dict:
    """Render `data/longform/{run_id}/script.json` into a reviewable MP4."""
    data_dir = Path(data_dir)
    work_dir = data_dir / "longform" / run_id
    script_file = work_dir / "script.json"
    if not script_file.is_file():
        raise FileNotFoundError(f"longform script.json이 없습니다: {script_file}")
    script = validate_longform_script(
        json.loads(script_file.read_text(encoding="utf-8"))
    )

    with tempfile.TemporaryDirectory(prefix="shorts-factory-longform-") as tmpdir:
        tmp_path = Path(tmpdir)
        mark_temp_owner(tmp_path)
        tts_results = []
        scene_videos = []
        scene_starts = {}
        audio_durations = {}
        cursor = 0.0
        tempo = story_playback_tempo()
        for index, scene in enumerate(script["scenes"], start=1):
            raw = tmp_path / f"narration-{index:02d}.mp3"
            wav = tmp_path / f"narration-{index:02d}.wav"
            result, duration = _prepare_narration(
                _tts_text(scene["narration"]), raw, wav, ffmpeg_path
            )
            if tempo != 1.0:
                from app.agents.story_producer import _retime_audio

                _retime_audio(wav, tempo, ffmpeg_path)
                duration = _duration(wav, ffmpeg_path)
            tts_results.append(result)
            scene_duration = max(float(scene["duration_sec"]), duration + 0.2)
            audio_durations[scene["n"]] = duration
            scene_starts[scene["n"]] = cursor
            cursor += scene_duration

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
            scene_video = tmp_path / f"scene-{index:02d}.mp4"
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
        _concat_longform_files(scene_videos, concat_video, ffmpeg_path, tmp_path)
        srt_path = tmp_path / "longform.srt"
        _write_longform_srt(script, scene_starts, audio_durations, srt_path)
        output_mp4 = work_dir / LONGFORM_OUTPUT
        _finish_longform(concat_video, output_mp4, srt_path, ffmpeg_path, tmp_path)
        actual_duration = _duration(output_mp4, ffmpeg_path)

    ai_assets = _reusable_ai_assets(script, data_dir, run_id)
    produce_log = {
        "date": run_id,
        "timestamp": datetime.now().isoformat(),
        "format": "longform",
        "output_file": str(output_mp4.resolve()),
        "planned_duration": script["total_duration_sec"],
        "actual_duration": round(actual_duration, 1),
        "script_sha256": hashlib.sha256(script_file.read_bytes()).hexdigest(),
        "click_package": _longform_click_package(script),
        "chapter_titles": [
            scene["chapter_title"] for scene in script.get("scenes", [])
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
    }
    (work_dir / "produce_log.json").write_text(
        json.dumps(produce_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return produce_log
