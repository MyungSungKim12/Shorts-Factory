"""영상 프로듀서 에이전트 — output.mp4 생성 (간단 버전)."""
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from gtts import gTTS
import requests

from app.services.image_downloader import download_video, download_image


async def run_producer(data_dir: Path, date_str: str, ffmpeg_path: str) -> dict:
    """
    script.json을 받아 output.mp4를 생성한다 (간단 버전).

    Args:
        data_dir: 데이터 저장 경로
        date_str: YYYYMMDD 형식 날짜
        ffmpeg_path: ffmpeg 실행 파일 경로

    Returns:
        produce_log.json dict
    """
    work_dir = data_dir / "work" / date_str
    script_file = work_dir / "script.json"

    if not script_file.exists():
        raise FileNotFoundError(f"script.json이 없습니다: {script_file}")

    script = json.loads(script_file.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. TTS 생성
        print("  → TTS 음성 생성 중...")
        mp3_files = await _generate_tts(script, tmp_path)

        # 2. 비디오 다운로드
        print("  → 비디오 다운로드 중...")
        video_files = await _download_videos(script, tmp_path)

        # 3. 각 씬을 mp4로 인코딩 (비디오 + 음성)
        print("  → 씬별 영상 생성 중...")
        scene_videos = []
        for scene in script.get("scenes", []):
            scene_n = scene["n"]
            mp3_file = mp3_files.get(scene_n)
            video_file = video_files.get(scene_n)

            if not (mp3_file and video_file):
                continue

            # 입력(다운로드 원본)과 출력 파일명이 겹치지 않게 enc_ 접두어 사용
            scene_video = tmp_path / f"enc_{scene_n}.mp4"
            _encode_scene_video(
                str(video_file), str(mp3_file), str(scene_video), ffmpeg_path,
                rank=scene.get("rank"),
            )
            scene_videos.append((scene_n, scene_video))

        if not scene_videos:
            raise RuntimeError("인코딩된 씬이 없습니다")

        # 4. 자막 생성 (씬별 실제 길이 측정 → 타이밍 정확한 SRT)
        print("  → 자막 생성 중...")
        _build_srt(script, scene_videos, ffmpeg_path, tmp_path / "subs.srt")

        # 5. 씬들을 연결 + 자막 굽기
        print("  → 씬 연결 및 자막 합성 중...")
        output_mp4 = work_dir / "output.mp4"
        _concat_videos(scene_videos, str(output_mp4), ffmpeg_path, tmp_path)

        # 5. 로그 저장
        produce_log = {
            "date": date_str,
            "timestamp": datetime.now().isoformat(),
            "output_file": str(output_mp4),
            "scenes_processed": len(scene_videos),
        }

        log_file = work_dir / "produce_log.json"
        log_file.write_text(json.dumps(produce_log, ensure_ascii=False, indent=2), encoding="utf-8")

        return produce_log


async def _generate_tts(script: dict, tmp_path: Path) -> dict:
    """각 씬별로 TTS mp3 파일 생성 (한국어)."""
    mp3_files = {}

    for scene in script.get("scenes", []):
        scene_n = scene["n"]
        narration = scene["narration"]
        mp3_file = tmp_path / f"scene_{scene_n}.mp3"

        # gTTS로 음성 생성 (한국어 고정)
        tts = gTTS(text=narration, lang='ko', slow=False)
        tts.save(str(mp3_file))

        mp3_files[scene_n] = mp3_file

    return mp3_files


async def _download_videos(script: dict, tmp_path: Path) -> dict:
    """각 씬별 visual 키워드로 Pexels 비디오 다운로드."""
    video_files = {}

    for scene in script.get("scenes", []):
        scene_n = scene["n"]
        visual_keyword = scene.get("visual", "background")

        video_file = tmp_path / f"scene_{scene_n}.mp4"

        # Pexels 비디오 다운로드
        try:
            await download_video(visual_keyword, str(video_file))
            video_files[scene_n] = video_file
        except Exception as e:
            print(f"  ⚠️ 씬 {scene_n} 비디오 다운로드 실패: {e}")
            # 폴백: 이미지로 대체
            image_file = tmp_path / f"scene_{scene_n}.jpg"
            try:
                await download_image(visual_keyword, str(image_file))
                video_files[scene_n] = image_file
            except:
                _create_black_bg(image_file)
                video_files[scene_n] = image_file

    return video_files


def _run_ffmpeg(cmd: list, cwd: str = None) -> None:
    """ffmpeg 실행 — 실패 시 stderr 마지막 부분을 에러 메시지에 포함."""
    result = subprocess.run(cmd, capture_output=True, cwd=cwd)
    if result.returncode != 0:
        stderr_tail = result.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg 실패 (코드 {result.returncode}):\n{stderr_tail}")


def _ffprobe_path(ffmpeg_path: str) -> str:
    """ffmpeg 경로에서 ffprobe 경로 유도 (Windows .exe / 리눅스 모두 지원)."""
    if ffmpeg_path.endswith("ffmpeg.exe"):
        return ffmpeg_path[: -len("ffmpeg.exe")] + "ffprobe.exe"
    if ffmpeg_path.endswith("ffmpeg"):
        return ffmpeg_path[: -len("ffmpeg")] + "ffprobe"
    return "ffprobe"


def _media_duration(path: str, ffmpeg_path: str) -> float:
    """ffprobe로 미디어 파일의 실제 길이(초)를 측정."""
    ffprobe = _ffprobe_path(ffmpeg_path)
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


_HANGUL_RANKS = {
    "십위": "10위", "구위": "9위", "팔위": "8위", "칠위": "7위", "육위": "6위",
    "오위": "5위", "사위": "4위", "삼위": "3위", "이위": "2위", "일위": "1위",
}


def _clean_caption(text: str) -> str:
    """자막용 텍스트 정리 — 한글 순위 표기를 숫자로 (발음용 원문은 유지)."""
    for hangul, digit in _HANGUL_RANKS.items():
        text = text.replace(hangul, digit)
    return text


def _build_srt(script: dict, scene_videos: list, ffmpeg_path: str, srt_path: Path) -> None:
    """씬별 인코딩 결과의 실제 길이를 측정해 타이밍 정확한 SRT 자막 생성."""
    narrations = {s["n"]: _clean_caption(s["narration"]) for s in script.get("scenes", [])}
    planned = {s["n"]: float(s.get("duration_sec", 5)) for s in script.get("scenes", [])}

    lines = []
    current = 0.0
    for idx, (scene_n, video_path) in enumerate(scene_videos, start=1):
        duration = _media_duration(str(video_path), ffmpeg_path)
        if duration <= 0:
            # 측정 실패 시 대본의 계획 길이로 대체 (자막이 0초가 되는 사고 방지)
            duration = planned.get(scene_n, 5.0)
        text = narrations.get(scene_n, "")
        if not text:
            current += duration
            continue

        lines.append(str(idx))
        lines.append(f"{_srt_time(current)} --> {_srt_time(current + duration)}")
        lines.append(text)
        lines.append("")
        current += duration

    srt_path.write_text("\n".join(lines), encoding="utf-8")


def _srt_time(seconds: float) -> str:
    """SRT 형식 시간 (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _overlay_fontfile() -> str:
    """순위 숫자 오버레이용 폰트 파일 탐색 (Windows/리눅스)."""
    candidates = [
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""


def _encode_scene_video(media_file: str, mp3_file: str, output_mp4: str, ffmpeg_path: str, rank=None) -> None:
    """한 씬을 비디오/이미지 + 음성으로 mp4 인코딩. rank가 있으면 순위 숫자 오버레이."""
    # 미디어 파일이 .mp4면 비디오, .jpg면 이미지
    is_video = media_file.lower().endswith('.mp4')

    # 나레이션 배속 (1.0=원속, 1.2=20% 빠르게 — 숏츠 속도감)
    tts_speed = os.getenv("TTS_SPEED", "1.2")

    # 모든 씬을 동일 규격(1080x1920/30fps/44.1kHz)으로 통일 — concat 싱크 어긋남 방지
    # scale+crop: 화면을 꽉 채우고 넘치는 부분은 잘라냄 (레터박스 없음)
    normalize_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"

    # 순위 숫자 오버레이 (랭킹 포맷의 핵심 시각 요소 — 화면 상단 큰 숫자)
    fontfile = _overlay_fontfile()
    if rank is not None and fontfile:
        ff_escaped = fontfile.replace(":", "\\:")
        normalize_vf += (
            f",drawtext=fontfile='{ff_escaped}':text='{rank}'"
            ":fontsize=170:fontcolor=white:borderw=12:bordercolor=black@0.8"
            ":x=(w-text_w)/2:y=160"
        )

    if is_video:
        # 비디오(무한 반복) + 나레이션 — 나레이션 길이에 정확히 맞춤
        cmd = [
            ffmpeg_path,
            "-stream_loop", "-1",   # 영상이 나레이션보다 짧으면 반복 재생
            "-i", media_file,
            "-i", mp3_file,
            "-map", "0:v:0",        # 영상은 스톡 비디오에서
            "-map", "1:a:0",        # 소리는 나레이션만 (원본 소리 제거 보장)
            "-vf", normalize_vf,
            "-af", f"atempo={tts_speed}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-pix_fmt", "yuv420p",
            "-shortest",            # 나레이션이 끝나면 종료
            "-y",
            output_mp4,
        ]
    else:
        # 이미지 + 나레이션 (이미지 루프)
        cmd = [
            ffmpeg_path,
            "-loop", "1",
            "-i", media_file,
            "-i", mp3_file,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", normalize_vf,
            "-af", f"atempo={tts_speed}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-pix_fmt", "yuv420p",
            "-shortest",
            "-y",
            output_mp4,
        ]

    _run_ffmpeg(cmd)


def _concat_videos(scene_videos: list, output_mp4: str, ffmpeg_path: str, tmp_path: Path) -> None:
    """모든 씬을 연결해서 최종 mp4 생성."""
    # concat demuxer 파일 생성
    concat_file = tmp_path / "concat.txt"
    concat_lines = [f"file '{sv[1]}'" for sv in scene_videos]
    concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

    # concat으로 연결
    concat_output = tmp_path / "concat.mp4"
    cmd = [
        ffmpeg_path,
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-y",
        str(concat_output),
    ]
    _run_ffmpeg(cmd)

    # 최종: 1080x1920 리사이징 + 자막 굽기 + 인코딩
    # (subtitles 필터는 Windows 절대경로 처리가 까다로워 cwd를 tmp로 두고 상대경로 사용)
    # 씬들이 이미 1080x1920/30fps로 통일돼 있으므로 여기선 자막 굽기 + BGM 합성만
    # 폰트: Windows=Malgun Gothic, 리눅스 서버=NanumGothic (.env로 변경)
    subtitle_font = os.getenv("SUBTITLE_FONT", "Malgun Gothic")
    subtitle_style = (
        f"FontName={subtitle_font},FontSize=13,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=70"
    )
    if (tmp_path / "subs.srt").exists():
        vf = f"subtitles=subs.srt:force_style='{subtitle_style}'"
    else:
        vf = "null"

    encode_opts = [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        # cwd가 tmp로 바뀌므로 출력은 절대 경로로 지정
        str(Path(output_mp4).resolve()),
    ]

    bgm_file = _pick_bgm()
    if bgm_file:
        # BGM을 나레이션 밑에 낮은 볼륨으로 깔기 (영상 길이에 맞춰 반복)
        bgm_volume = os.getenv("BGM_VOLUME", "0.12")
        cmd = [
            ffmpeg_path,
            "-i", str(concat_output),
            "-stream_loop", "-1",
            "-i", str(bgm_file),
            "-filter_complex",
            f"[0:v]{vf}[vout];"
            f"[1:a]volume={bgm_volume}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
        ] + encode_opts
    else:
        cmd = [ffmpeg_path, "-i", str(concat_output), "-vf", vf] + encode_opts

    _run_ffmpeg(cmd, cwd=str(tmp_path))


def _pick_bgm():
    """assets/bgm/ 폴더의 음악 중 하나를 날짜 기준으로 순환 선택 (없으면 BGM 생략)."""
    bgm_dir = Path("assets/bgm")
    if not bgm_dir.exists():
        return None
    tracks = sorted(bgm_dir.glob("*.mp3")) + sorted(bgm_dir.glob("*.m4a"))
    if not tracks:
        return None
    idx = datetime.now().timetuple().tm_yday % len(tracks)
    return tracks[idx].resolve()


def _create_black_bg(output_file: Path) -> None:
    """검은 배경 이미지 생성."""
    try:
        from PIL import Image
        img = Image.new("RGB", (1080, 1920), color="black")
        img.save(output_file)
    except ImportError:
        # PIL 없으면 스킵 (ffmpeg가 처리)
        pass
