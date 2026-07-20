"""단일 소재 스토리형 Shorts 렌더러."""
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.console import safe_print
from app.services.media_library import fetch_story_media
from app.services.tts import TTSResult, synthesize


DEFAULT_STORY_CTA = "이런 이야기가 더 궁금하다면, 구독과 좋아요 부탁드립니다."


STORY_LAYOUT = {
    "canvas_width": 1080,
    "canvas_height": 1920,
    "top_band": 260,
    "video_height": 1330,
    "bottom_band": 330,
}


def normalize_story_cta(value: str | None) -> tuple[str, bool]:
    """주제 맞춤 CTA가 두 행동을 모두 포함하지 않으면 안전한 기본 문구를 쓴다."""
    text = (value or "").strip()
    if "구독" not in text or "좋아요" not in text:
        return DEFAULT_STORY_CTA, True
    return text, False


def build_cta_timing(body_duration: float, audio_duration: float) -> dict[str, float]:
    """본문 직후 CTA를 배치하고 최종 Shorts 길이 범위를 검증한다."""
    start = round(float(body_duration), 3)
    end = round(start + float(audio_duration), 3)
    if end < 60:
        raise RuntimeError(f"CTA 포함 최종 길이 {end:.1f}초로 60초 미만")
    if end > 75:
        raise RuntimeError(f"CTA 포함 최종 길이 {end:.1f}초로 75초 초과")
    return {"start": start, "end": end, "total_duration": end}


def build_story_timing(
    intro_audio_duration: float,
    body_duration: float,
    cta_audio_duration: float,
    padding: float = 0.15,
) -> dict[str, float]:
    """Place the spoken title before the body and validate the final Shorts length."""
    intro_duration = round(float(intro_audio_duration) + float(padding), 3)
    body_start = intro_duration
    cta_start = round(body_start + float(body_duration), 3)
    cta_end = round(cta_start + float(cta_audio_duration), 3)
    if cta_end < 60:
        raise RuntimeError(f"인트로·CTA 포함 최종 길이 {cta_end:.1f}초로 60초 미만")
    if cta_end > 75:
        raise RuntimeError(f"인트로·CTA 포함 최종 길이 {cta_end:.1f}초로 75초 초과")
    return {
        "intro_duration": intro_duration,
        "body_start": body_start,
        "cta_start": cta_start,
        "cta_end": cta_end,
        "total_duration": cta_end,
    }


def _scene_shots(scene: dict, duration: float | None = None) -> list[dict]:
    visuals = [value.strip() for value in scene.get("visuals", []) if value.strip()]
    if not visuals:
        visuals = ["natural landscape"]
    total = float(duration if duration is not None else scene.get("duration_sec", 4))
    count = max(len(visuals), math.ceil(total / 4))
    seconds = max(2.0, min(4.0, total / count))
    shots = []
    remaining = total
    for index in range(count):
        shot_duration = seconds if index < count - 1 else max(2.0, remaining)
        shot_duration = min(4.0, shot_duration)
        shots.append({
            "scene_n": scene["n"],
            "role": scene.get("role", "context"),
            "keyword": visuals[index % len(visuals)],
            "duration_sec": round(shot_duration, 3),
        })
        remaining -= shot_duration
    return shots


def build_shot_plan(script: dict) -> list[dict]:
    """각 비트를 2~4초 시각 샷으로 나눈다."""
    shots = []
    for scene in script.get("scenes", []):
        shots.extend(_scene_shots(scene))
    for shot_n, shot in enumerate(shots, start=1):
        shot["shot_n"] = shot_n
    return shots


def visual_filter(
    media_file: str,
    duration: float,
    preserve_full: bool = False,
    darken: bool = False,
) -> str:
    """세로 전체화면 영상 또는 정지 이미지 모션 필터를 만든다."""
    overlay = ",drawbox=color=black@0.35:t=fill" if darken else ""
    pad = ",pad=1080:1920:0:260:black"
    if str(media_file).lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        frames = max(1, round(duration * 30))
        if preserve_full:
            return (
                "[0:v]split=2[bgsrc][fgsrc];"
                "[bgsrc]scale=1080:1330:force_original_aspect_ratio=increase,"
                "crop=1080:1330,boxblur=luma_radius=28:luma_power=2[bg];"
                "[fgsrc]scale=1080:1330:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                "zoompan=z='min(zoom+0.0003,1.03)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s=1080x1330:fps=30{overlay}{pad},format=yuv420p[vout]"
            )
        return (
            "scale=1200:1478:force_original_aspect_ratio=increase,"
            "crop=1200:1478,"
            "zoompan=z='min(zoom+0.0008,1.08)':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s=1080x1330:fps=30{overlay}{pad},format=yuv420p"
        )
    return (
        "scale=1080:1330:force_original_aspect_ratio=increase,"
        f"crop=1080:1330,fps=30{overlay}{pad},format=yuv420p"
    )


def summarize_tts(results: list[TTSResult]) -> dict:
    providers = {result.provider for result in results}
    if len(providers) == 1:
        first = results[0]
        return {
            "provider": first.provider,
            "voice": first.voice,
            "speaking_rate": first.speaking_rate,
        }
    return {
        "provider": "mixed",
        "voice": ",".join(sorted({result.voice for result in results})),
        "speaking_rate": None,
    }


def _run_ffmpeg(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, cwd=cwd)
    if result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"ffmpeg 실패({result.returncode}):\n{stderr}")


def _ffprobe_path(ffmpeg_path: str) -> str:
    lower = ffmpeg_path.lower()
    if lower.endswith("ffmpeg.exe"):
        return ffmpeg_path[:-10] + "ffprobe.exe"
    if lower.endswith("ffmpeg"):
        return ffmpeg_path[:-6] + "ffprobe"
    return "ffprobe"


def _duration(path: Path, ffmpeg_path: str) -> float:
    result = subprocess.run(
        [_ffprobe_path(ffmpeg_path), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


_UNIT_RULES = [
    (re.compile(r"km²|㎢|km2"), "제곱킬로미터"),
    (re.compile(r"m²|㎡|m2"), "제곱미터"),
    (re.compile(r"(?<=\d)\s*km/h"), "킬로미터퍼아워"),
    (re.compile(r"(?<=\d)\s*km(?![a-zA-Z])"), "킬로미터"),
    (re.compile(r"(?<=\d)\s*m(?![a-zA-Z²³])"), "미터"),
    (re.compile(r"(?<=\d)\s*%"), "퍼센트"),
    (re.compile(r"(?<=\d)\s*℃"), "도"),
]


def _tts_text(text: str) -> str:
    for pattern, replacement in _UNIT_RULES:
        text = pattern.sub(replacement, text)
    return text


def _create_fallback_image(path: Path, scene_n: int) -> None:
    """검은 화면 대신 차분한 청색 그라디언트 폴백을 만든다."""
    image = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(image)
    for y in range(1920):
        ratio = y / 1919
        color = (18 + int(12 * ratio), 34 + int(24 * ratio), 54 + int(30 * ratio))
        draw.line((0, y, 1080, y), fill=color)
    radius = 170 + scene_n * 8
    draw.ellipse((540 - radius, 960 - radius, 540 + radius, 960 + radius), fill=(35, 76, 105))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=92)


def _encode_visual(
    media: Path,
    output: Path,
    duration: float,
    ffmpeg_path: str,
    preserve_full: bool = False,
    darken: bool = False,
) -> None:
    is_image = media.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    cmd = [ffmpeg_path]
    if is_image:
        cmd += ["-loop", "1", "-i", str(media)]
    else:
        cmd += ["-stream_loop", "-1", "-i", str(media)]
    cmd += ["-an"]
    if preserve_full and is_image:
        cmd += [
            "-filter_complex", visual_filter(str(media), duration, preserve_full, darken),
            "-map", "[vout]",
        ]
    else:
        cmd += ["-vf", visual_filter(str(media), duration, preserve_full, darken)]
    cmd += [
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-r", "30", "-y", str(output),
    ]
    _run_ffmpeg(cmd)


def _concat_files(files: list[Path], output: Path, ffmpeg_path: str, tmp_path: Path) -> None:
    manifest = tmp_path / f"{output.stem}-concat.txt"
    lines = [f"file '{path.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in files]
    manifest.write_text("\n".join(lines), encoding="utf-8")
    _run_ffmpeg([
        ffmpeg_path, "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-c", "copy", "-y", str(output),
    ])


def _attach_narration(
    visual: Path,
    narration: Path,
    output: Path,
    duration: float,
    ffmpeg_path: str,
) -> None:
    _run_ffmpeg([
        ffmpeg_path, "-i", str(visual), "-i", str(narration),
        "-filter_complex", "[1:a]apad[aout]",
        "-map", "0:v:0", "-map", "[aout]", "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-y", str(output),
    ])


def _split_caption(text: str, max_len: int = 22) -> list[str]:
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and len(candidate) > max_len:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
    return chunks or [text]


def _wrap_title(text: str, max_chars: int = 18, max_lines: int = 2) -> list[str]:
    """Wrap a title without ever splitting a word; overflow stays on the last line."""
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current: list[str] = []
    for index, word in enumerate(words):
        if len(lines) == max_lines - 1:
            current.extend(words[index:])
            break

        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)

    if current:
        lines.append(" ".join(current))
    return lines[:max_lines]


def _title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    configured = os.getenv("SUBTITLE_FONT_FILE")
    candidates = [
        configured,
        "C:/Windows/Fonts/malgunbd.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _create_title_overlay(title: str, output: Path) -> dict[str, int]:
    """Create a transparent full-canvas PNG with a fixed title in the top band."""
    lines = _wrap_title(title, max_chars=18, max_lines=2)
    text = "\n".join(lines)
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    selected_size = 34
    selected_font = _title_font(selected_size)
    for size in range(58, 33, -2):
        font = _title_font(size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=10, align="center")
        if bbox[2] - bbox[0] <= 980 and bbox[3] - bbox[1] <= 220:
            selected_size = size
            selected_font = font
            break

    draw.multiline_text(
        (540, STORY_LAYOUT["top_band"] / 2),
        text,
        font=selected_font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        align="center",
        spacing=10,
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return {"line_count": len(lines), "font_size": selected_size}


def _srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds % 1) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(
    script: dict,
    scene_durations: dict[int, float],
    audio_durations: dict[int, float],
    output: Path,
    cta: dict | None = None,
    intro: dict | None = None,
) -> None:
    lines = []
    cue = 0
    current = 0.0
    if intro:
        cue += 1
        lines.extend([
            str(cue),
            f"{_srt_time(0)} --> {_srt_time(intro['audio_end'])}",
            intro["text"],
            "",
        ])
        current = float(intro["body_start"])
    for scene in script.get("scenes", []):
        scene_duration = scene_durations[scene["n"]]
        caption_duration = min(scene_duration, audio_durations[scene["n"]])
        chunks = _split_caption(scene["narration"])
        weights = [max(1, len(chunk)) for chunk in chunks]
        total_weight = sum(weights)
        cursor = current
        for chunk, weight in zip(chunks, weights):
            chunk_duration = caption_duration * weight / total_weight
            cue += 1
            lines.extend([
                str(cue),
                f"{_srt_time(cursor)} --> {_srt_time(cursor + chunk_duration)}",
                chunk,
                "",
            ])
            cursor += chunk_duration
        current += scene_duration
    if cta:
        cue += 1
        lines.extend([
            str(cue),
            f"{_srt_time(cta['start'])} --> {_srt_time(cta['end'])}",
            cta["text"],
            "",
        ])
    output.write_text("\n".join(lines), encoding="utf-8")


def _subtitle_style(font: str) -> str:
    return (
        f"FontName={font},FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,Outline=3,Shadow=1,Alignment=2,MarginV=110"
    )


def _pick_bgm() -> Path | None:
    directory = Path("assets/bgm")
    if not directory.exists():
        return None
    tracks = sorted(directory.glob("*.mp3")) + sorted(directory.glob("*.m4a"))
    if not tracks:
        return None
    return tracks[datetime.now().timetuple().tm_yday % len(tracks)].resolve()


def _finish_video(
    concat_video: Path,
    output: Path,
    srt_path: Path,
    title_overlay: Path,
    ffmpeg_path: str,
    tmp_path: Path,
) -> None:
    font = os.getenv("SUBTITLE_FONT", "Malgun Gothic")
    style = _subtitle_style(font)
    video_filter = f"subtitles=subs.srt:force_style='{style}'"
    bgm = _pick_bgm()
    if bgm:
        volume = os.getenv("BGM_VOLUME", "0.08")
        cmd = [
            ffmpeg_path, "-i", str(concat_video), "-stream_loop", "-1", "-i", str(bgm),
            "-i", str(title_overlay),
            "-filter_complex",
            f"[0:v]{video_filter}[subbed];[subbed][2:v]overlay=0:0[vout];"
            f"[1:a]volume={volume}[bg];"
            "[bg][0:a]sidechaincompress=threshold=0.02:ratio=8[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[vout]", "-map", "[aout]",
        ]
    else:
        cmd = [
            ffmpeg_path, "-i", str(concat_video), "-i", str(title_overlay),
            "-filter_complex",
            f"[0:v]{video_filter}[subbed];[subbed][1:v]overlay=0:0[vout]",
            "-map", "[vout]", "-map", "0:a:0",
        ]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-y", str(output.resolve()),
    ]
    _run_ffmpeg(cmd, cwd=tmp_path)


async def run_story_producer(
    data_dir: Path,
    run_id: str,
    ffmpeg_path: str,
    work_root: str = "work",
) -> dict:
    """스토리 대본을 1080x1920 MP4로 제작하고 전체 출처를 기록한다."""
    work_dir = Path(data_dir) / work_root / run_id
    script_file = work_dir / "script.json"
    if not script_file.exists():
        raise FileNotFoundError(f"script.json이 없습니다: {script_file}")
    script = json.loads(script_file.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tts_results = []
        narration_files = {}
        scene_durations = {}
        audio_durations = {}

        intro_narration = tmp_path / "narration-intro.mp3"
        intro_result = synthesize(_tts_text(script["title"]), intro_narration)
        tts_results.append(intro_result)
        intro_audio_duration = _duration(intro_narration, ffmpeg_path)

        safe_print("  → Neural2 스토리 나레이션 생성 중...")
        for scene in script.get("scenes", []):
            narration = tmp_path / f"narration-{scene['n']:02d}.mp3"
            result = synthesize(_tts_text(scene["narration"]), narration)
            tts_results.append(result)
            narration_files[scene["n"]] = narration
            audio_duration = _duration(narration, ffmpeg_path)
            audio_durations[scene["n"]] = audio_duration
            scene_durations[scene["n"]] = max(
                float(scene["duration_sec"]), round(audio_duration + 0.2, 3)
            )

        body_duration = sum(scene_durations.values())
        cta_text, cta_fallback = normalize_story_cta(script.get("cta"))
        cta_narration = tmp_path / "narration-cta.mp3"
        cta_result = synthesize(_tts_text(cta_text), cta_narration)
        tts_results.append(cta_result)
        cta_audio_duration = _duration(cta_narration, ffmpeg_path)
        story_timing = build_story_timing(
            intro_audio_duration, body_duration, cta_audio_duration
        )

        safe_print("  → 무료 미디어 선별 및 2~4초 샷 생성 중...")
        used_ids: set[str] = set()
        sources = []
        scene_videos = []
        global_shot_n = 0
        last_media = None
        last_metadata = {}
        first_media = None
        first_metadata = {}
        for scene in script.get("scenes", []):
            shots = _scene_shots(scene, scene_durations[scene["n"]])
            visual_clips = []
            for local_index, shot in enumerate(shots, start=1):
                global_shot_n += 1
                other_keywords = [
                    value for value in scene.get("visuals", []) if value != shot["keyword"]
                ]
                media, metadata = await fetch_story_media(
                    [shot["keyword"], *other_keywords],
                    tmp_path / f"media-{global_shot_n:03d}",
                    used_ids,
                )
                if media is None:
                    media = tmp_path / f"fallback-{global_shot_n:03d}.jpg"
                    _create_fallback_image(media, scene["n"])
                if first_media is None:
                    first_media = media
                    first_metadata = metadata
                last_media = media
                last_metadata = metadata
                clip = tmp_path / f"shot-{global_shot_n:03d}.mp4"
                _encode_visual(
                    media,
                    clip,
                    shot["duration_sec"],
                    ffmpeg_path,
                    preserve_full=metadata.get("provider") == "wikimedia_image",
                )
                visual_clips.append(clip)
                sources.append({
                    "scene": scene["n"],
                    "shot": global_shot_n,
                    "duration_sec": shot["duration_sec"],
                    **metadata,
                })

            scene_visual = tmp_path / f"scene-visual-{scene['n']:02d}.mp4"
            _concat_files(visual_clips, scene_visual, ffmpeg_path, tmp_path)
            scene_video = tmp_path / f"scene-{scene['n']:02d}.mp4"
            _attach_narration(
                scene_visual,
                narration_files[scene["n"]],
                scene_video,
                scene_durations[scene["n"]],
                ffmpeg_path,
            )
            scene_videos.append(scene_video)

        if first_media is None or last_media is None:
            raise RuntimeError("CTA 엔딩에 재사용할 마지막 시각 소스가 없습니다")
        intro_visual = tmp_path / "intro-visual.mp4"
        _encode_visual(
            first_media,
            intro_visual,
            story_timing["intro_duration"],
            ffmpeg_path,
            preserve_full=first_metadata.get("provider") == "wikimedia_image",
        )
        intro_video = tmp_path / "scene-intro.mp4"
        _attach_narration(
            intro_visual,
            intro_narration,
            intro_video,
            story_timing["intro_duration"],
            ffmpeg_path,
        )
        scene_videos.insert(0, intro_video)

        cta_visual = tmp_path / "cta-visual.mp4"
        _encode_visual(
            last_media,
            cta_visual,
            cta_audio_duration,
            ffmpeg_path,
            preserve_full=last_metadata.get("provider") == "wikimedia_image",
            darken=True,
        )
        cta_video = tmp_path / "scene-cta.mp4"
        _attach_narration(
            cta_visual,
            cta_narration,
            cta_video,
            cta_audio_duration,
            ffmpeg_path,
        )
        scene_videos.append(cta_video)

        concat_video = tmp_path / "story-concat.mp4"
        _concat_files(scene_videos, concat_video, ffmpeg_path, tmp_path)
        srt_path = tmp_path / "subs.srt"
        _write_srt(
            script,
            scene_durations,
            audio_durations,
            srt_path,
            intro={
                "text": script["title"],
                "audio_end": intro_audio_duration,
                "body_start": story_timing["body_start"],
            },
            cta={
                "text": cta_text,
                "start": story_timing["cta_start"],
                "end": story_timing["cta_end"],
            },
        )
        title_overlay = tmp_path / "title-overlay.png"
        title_metadata = _create_title_overlay(script["title"], title_overlay)

        output_mp4 = work_dir / "output.mp4"
        _finish_video(
            concat_video, output_mp4, srt_path, title_overlay, ffmpeg_path, tmp_path
        )
        actual_duration = _duration(output_mp4, ffmpeg_path)

    produce_log = {
        "date": run_id,
        "timestamp": datetime.now().isoformat(),
        "format": "story",
        "output_file": str(output_mp4.resolve()),
        "planned_duration": script.get("total_duration_sec", 0),
        "actual_duration": round(actual_duration, 1),
        "script_sha256": hashlib.sha256(script_file.read_bytes()).hexdigest(),
        "tts": summarize_tts(tts_results),
        "layout": {**STORY_LAYOUT, "title": title_metadata},
        "intro": {
            "text": script["title"],
            "audio_duration": round(intro_audio_duration, 3),
            "duration": story_timing["intro_duration"],
            "tts": {
                "provider": intro_result.provider,
                "voice": intro_result.voice,
                "speaking_rate": intro_result.speaking_rate,
            },
            "visual_source": first_metadata,
        },
        "cta": {
            "text": cta_text,
            "audio_duration": round(cta_audio_duration, 3),
            "fallback_used": cta_fallback,
            "tts": {
                "provider": cta_result.provider,
                "voice": cta_result.voice,
                "speaking_rate": cta_result.speaking_rate,
            },
            "visual_source": last_metadata,
        },
        "sources": sources,
        "fallback_shots": sum(1 for item in sources if item.get("fallback")),
        "experiment": "story_v1_retention",
    }
    (work_dir / "produce_log.json").write_text(
        json.dumps(produce_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return produce_log
