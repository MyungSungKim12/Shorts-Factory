"""영상 프로듀서 에이전트 — output.mp4 생성 (간단 버전)."""
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from gtts import gTTS
import requests

from app.services.image_downloader import download_video, download_video_pixabay, download_image


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

        # 3. 오버레이 준비 — 상단 타이틀 + 좌측 누적 순위 리스트용 데이터
        topic_title = ""
        items_map = {}
        ranking_size = 0
        topic_file = work_dir / "topic.json"
        if topic_file.exists():
            try:
                t = json.loads(topic_file.read_text(encoding="utf-8"))
                topic_title = t.get("topic", "")
                items_map = {i["rank"]: i["name"] for i in t.get("items", [])}
                ranking_size = t.get("ranking_size", len(items_map))
            except (json.JSONDecodeError, OSError):
                pass

        scenes = script.get("scenes", [])
        # 순위별 "공개 시점" = 그 순위가 등장하는 마지막 씬
        # (1위 직전 긴장 씬도 rank=1일 수 있어, 마지막 등장 씬 전까지는 이름을 비워 스포일러 방지)
        reveal_at = {}
        for idx, s in enumerate(scenes):
            if s.get("rank"):
                reveal_at[s["rank"]] = idx

        fontfile = _overlay_fontfile()

        # 4. 각 씬을 mp4로 인코딩 (비디오 + 음성 + 오버레이)
        print("  → 씬별 영상 생성 중...")
        scene_videos = []
        for idx, scene in enumerate(scenes):
            scene_n = scene["n"]
            mp3_file = mp3_files.get(scene_n)
            video_file = video_files.get(scene_n)

            if not (mp3_file and video_file):
                continue

            revealed = {r for r, at in reveal_at.items() if at <= idx}
            extra_vf = ""
            if items_map and fontfile:
                extra_vf = _build_scene_overlay(
                    topic_title, items_map, ranking_size, revealed,
                    scene.get("rank"), fontfile,
                )

            # 입력(다운로드 원본)과 출력 파일명이 겹치지 않게 enc_ 접두어 사용
            scene_video = tmp_path / f"enc_{scene_n}.mp4"
            _encode_scene_video(
                str(video_file), str(mp3_file), str(scene_video), ffmpeg_path,
                extra_vf=extra_vf,
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

        # 6. 로그 저장
        produce_log = {
            "date": date_str,
            "timestamp": datetime.now().isoformat(),
            "output_file": str(output_mp4),
            "scenes_processed": len(scene_videos),
        }

        log_file = work_dir / "produce_log.json"
        log_file.write_text(json.dumps(produce_log, ensure_ascii=False, indent=2), encoding="utf-8")

        return produce_log


# 단위·기호 → TTS 발음용 한글. 자막에는 원본 기호가 그대로 보이고, 음성만 이 변환을 거친다.
# (순서 중요: 제곱 단위를 먼저 처리해야 "제곱킬로미터"가 "킬로미터제곱"으로 깨지지 않음)
import re as _re

_TTS_UNIT_RULES = [
    (_re.compile(r'km²|㎢|km2'), '제곱킬로미터'),
    (_re.compile(r'm²|㎡|m2'), '제곱미터'),
    (_re.compile(r'km³|km3'), '세제곱킬로미터'),
    (_re.compile(r'm³|㎥|m3'), '세제곱미터'),
    (_re.compile(r'(?<=\d)\s*km/h'), '킬로미터퍼아워'),
    (_re.compile(r'(?<=\d)\s*km(?![a-zA-Z])'), '킬로미터'),
    (_re.compile(r'(?<=\d)\s*cm(?![a-zA-Z])'), '센티미터'),
    (_re.compile(r'(?<=\d)\s*mm(?![a-zA-Z])'), '밀리미터'),
    (_re.compile(r'(?<=\d)\s*m(?![a-zA-Z²³])'), '미터'),
    (_re.compile(r'(?<=\d)\s*kg(?![a-zA-Z])'), '킬로그램'),
    (_re.compile(r'(?<=\d)\s*%'), '퍼센트'),
    (_re.compile(r'(?<=\d)\s*℃'), '도'),
    (_re.compile(r'²'), '제곱'),
    (_re.compile(r'³'), '세제곱'),
]


def _tts_text(text: str) -> str:
    """나레이션을 TTS가 정확히 읽도록 단위 기호를 한글로 치환 (음성 전용)."""
    for pattern, repl in _TTS_UNIT_RULES:
        text = pattern.sub(repl, text)
    return text


async def _generate_tts(script: dict, tmp_path: Path) -> dict:
    """각 씬별로 TTS mp3 파일 생성 (한국어). 단위 기호는 발음용 한글로 치환."""
    mp3_files = {}

    for scene in script.get("scenes", []):
        scene_n = scene["n"]
        narration = _tts_text(scene["narration"])
        mp3_file = tmp_path / f"scene_{scene_n}.mp3"

        # gTTS로 음성 생성 (한국어 고정)
        tts = gTTS(text=narration, lang='ko', slow=False)
        tts.save(str(mp3_file))

        mp3_files[scene_n] = mp3_file

    return mp3_files


async def _download_videos(script: dict, tmp_path: Path) -> dict:
    """각 씬별 visual 키워드로 비디오 확보.

    폴백 순서: Pexels 비디오 → Pixabay 비디오 → Pexels 이미지 → 검은 배경.
    (무료 소스 2곳을 다 뒤져 실제 영상 확보율을 최대화)
    """
    video_files = {}
    have_pixabay = bool(os.getenv("PIXABAY_API_KEY"))

    for scene in script.get("scenes", []):
        scene_n = scene["n"]
        visual_keyword = scene.get("visual", "background")
        video_file = tmp_path / f"scene_{scene_n}.mp4"

        # 1) Pexels 비디오
        try:
            await download_video(visual_keyword, str(video_file))
            video_files[scene_n] = video_file
            continue
        except Exception:
            pass

        # 2) Pixabay 비디오
        if have_pixabay:
            try:
                await download_video_pixabay(visual_keyword, str(video_file))
                video_files[scene_n] = video_file
                print(f"  · 씬 {scene_n}: Pixabay 비디오로 확보")
                continue
            except Exception:
                pass

        # 3) 이미지 폴백 → 4) 검은 배경
        image_file = tmp_path / f"scene_{scene_n}.jpg"
        try:
            await download_image(visual_keyword, str(image_file))
            print(f"  · 씬 {scene_n}: 비디오 없음 → 이미지로 대체")
        except Exception:
            _create_black_bg(image_file)
            print(f"  ⚠️ 씬 {scene_n}: 소스 없음 → 검은 배경")
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


def _split_caption(text: str, max_len: int = 26) -> list:
    """긴 나레이션을 짧은 자막 조각으로 분할 (문장 우선, 길면 공백 기준).

    화면을 덮는 4줄짜리 벽 자막 대신 1~2줄씩 순차 표시하기 위함.
    """
    import re

    sentences = re.split(r'(?<=[.!?…]) +', text.strip())
    chunks = []
    for s in sentences:
        s = s.strip()
        while len(s) > max_len:
            cut = s.rfind(' ', 0, max_len)
            if cut < 10:  # 자를 공백이 마땅치 않으면 강제 분할
                cut = max_len
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            chunks.append(s)
    return chunks or [text]


def _build_srt(script: dict, scene_videos: list, ffmpeg_path: str, srt_path: Path) -> None:
    """씬별 실제 길이 측정 + 나레이션을 문장 단위로 분할해 순차 표시되는 SRT 생성."""
    narrations = {s["n"]: _clean_caption(s["narration"]) for s in script.get("scenes", [])}
    planned = {s["n"]: float(s.get("duration_sec", 5)) for s in script.get("scenes", [])}

    lines = []
    cue_no = 0
    current = 0.0
    for scene_n, video_path in scene_videos:
        duration = _media_duration(str(video_path), ffmpeg_path)
        if duration <= 0:
            # 측정 실패 시 대본의 계획 길이로 대체 (자막이 0초가 되는 사고 방지)
            duration = planned.get(scene_n, 5.0)
        text = narrations.get(scene_n, "")
        if not text:
            current += duration
            continue

        # 씬 길이를 조각별 글자수 비례로 배분 → 음성 진행과 자막이 대체로 동기화됨
        chunks = _split_caption(text)
        total_chars = sum(len(c) for c in chunks) or 1
        chunk_start = current
        for chunk in chunks:
            chunk_dur = duration * len(chunk) / total_chars
            cue_no += 1
            lines.append(str(cue_no))
            lines.append(f"{_srt_time(chunk_start)} --> {_srt_time(chunk_start + chunk_dur)}")
            lines.append(chunk)
            lines.append("")
            chunk_start += chunk_dur

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
    """오버레이용 폰트 파일 탐색 — 주아체(둥근 숏츠 폰트) 우선."""
    env_font = os.getenv("OVERLAY_FONTFILE", "")
    candidates = [
        env_font,
        "assets/fonts/Jua-Regular.ttf",                 # 프로젝트 동봉 (로컬/서버 공통)
        "/usr/local/share/fonts/Jua-Regular.ttf",       # 서버 설치본
        "C:/Windows/Fonts/malgunbd.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(Path(c).resolve())
    return ""


def _wrap_text(text: str, width: int) -> list:
    """공백 기준 그리디 줄바꿈."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [text]


# 순위별 색상 (경쟁 채널 스타일의 무지개 팔레트 — 1위가 가장 뜨거운 색)
_RANK_COLORS = {
    1: "0xFF5252",   # 빨강
    2: "0xFFA726",   # 주황
    3: "0xFFEB3B",   # 노랑
    4: "0x9CCC65",   # 연두
    5: "0x4FC3F7",   # 하늘
    6: "0xBA68C8",   # 보라
    7: "0xF48FB1",   # 분홍
    8: "0x4DB6AC",   # 청록
    9: "0xFF8A65",   # 코랄
    10: "0xB0BEC5",  # 회백
}


# 상·하단 검은 띠 높이(px). 영상은 이 사이 (1920 - BANNER_H - BANNER_BOTTOM_H) 영역에 배치된다.
BANNER_H = 320          # 상단: 타이틀 + 순위 리스트
BANNER_BOTTOM_H = 300   # 하단: 자막 영역 (영상을 안 가림)


def _build_scene_overlay(title: str, items: dict, ranking_size: int,
                         revealed: set, current_rank, fontfile: str) -> str:
    """상단 검은 띠 타이틀 + 좌측 누적 순위 리스트 drawtext 필터 체인 생성.

    - 타이틀: 상단 검은 띠(BANNER_H) 안 흰 글씨, 항상 표시 (배경과 무관하게 선명)
    - 리스트: 번호는 순위별 고유 색, 이름은 흰색. 공개된 순위만 이름 채움
    - 현재 순위 줄은 글자를 키워 강조
    """
    ff = fontfile.replace(":", "\\:")

    def esc(t: str) -> str:
        # drawtext 텍스트는 작은따옴표로 감싸므로 따옴표만 무력화하면 안전
        return t.replace("\\", "").replace("'", "’")

    filters = []

    # 상단 검은 띠 안 타이틀 (최대 2줄) — 띠 안에서 세로 중앙 정렬
    title_lines = _wrap_text(title, 15)[:2]
    line_h = 82
    block_h = len(title_lines) * line_h
    top = (BANNER_H - block_h) // 2
    for i, line in enumerate(title_lines):
        filters.append(
            f"drawtext=fontfile='{ff}':text='{esc(line)}':fontsize=64:fontcolor=white"
            f":borderw=4:bordercolor=black:x=(w-text_w)/2:y={top + i * line_h}"
        )

    # 좌측 누적 순위 리스트 (번호=순위색, 이름=흰색) — 검은 띠 바로 아래에서 시작, 크게
    list_y_start = BANNER_H + 40
    line_height = 78
    for r in range(1, ranking_size + 1):
        y = list_y_start + (r - 1) * line_height
        size = 62 if r == current_rank else 50  # 현재 순위 강조
        num_color = _RANK_COLORS.get(r, "white")

        filters.append(
            f"drawtext=fontfile='{ff}':text='{esc(str(r))}.':fontsize={size}"
            f":fontcolor={num_color}:borderw=6:bordercolor=black:x=44:y={y}"
        )

        name = items.get(r, "") if r in revealed else ""
        if name:
            if len(name) > 13:
                name = name[:12] + "…"
            filters.append(
                f"drawtext=fontfile='{ff}':text='{esc(name)}':fontsize={size}"
                f":fontcolor=white:borderw=6:bordercolor=black:x=150:y={y}"
            )

    return "," + ",".join(filters)


def _encode_scene_video(media_file: str, mp3_file: str, output_mp4: str, ffmpeg_path: str, extra_vf: str = "") -> None:
    """한 씬을 비디오/이미지 + 음성으로 mp4 인코딩. extra_vf로 오버레이 필터 추가."""
    # 미디어 파일이 .mp4면 비디오, .jpg면 이미지
    is_video = media_file.lower().endswith('.mp4')

    # 나레이션 배속 (1.0=원속, 1.2=20% 빠르게 — 숏츠 속도감)
    tts_speed = os.getenv("TTS_SPEED", "1.2")

    # 모든 씬을 동일 규격(1080x1920/30fps/44.1kHz)으로 통일 — concat 싱크 어긋남 방지
    if extra_vf:
        # 오버레이가 있으면: 영상을 상·하단 검은 띠 사이 영역에 채우고 위아래를 검게 패딩
        vid_h = 1920 - BANNER_H - BANNER_BOTTOM_H
        normalize_vf = (
            f"scale=1080:{vid_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{vid_h},pad=1080:1920:0:{BANNER_H}:black,fps=30"
        )
        normalize_vf += extra_vf
    else:
        # 오버레이 없으면 화면 꽉 채움 (레터박스 없음)
        normalize_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"

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


def _channel_name() -> str:
    """채널명 조회 (.env CHANNEL_NAME 우선, 없으면 config/channel.json)."""
    name = os.getenv("CHANNEL_NAME", "").strip()
    if name:
        return name
    cfg = Path("config/channel.json")
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("channel_name", "").strip()
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def _opening_filter() -> str:
    """첫 1.8초 채널명 오프닝 drawtext. 채널명·폰트 없으면 빈 문자열."""
    name = _channel_name()
    font = _overlay_fontfile()
    if not name or not font:
        return ""
    ff = font.replace(":", "\\:")
    safe = name.replace("\\", "").replace("'", "’").replace(":", "\\:")
    # 화면 중앙에 크게, 반투명 박스 위. enable로 0~1.8초에만 표시.
    return (
        f"drawtext=fontfile='{ff}':text='{safe}':fontsize=72:fontcolor=white"
        f":borderw=6:bordercolor=black:box=1:boxcolor=black@0.5:boxborderw=24"
        f":x=(w-text_w)/2:y=(h-text_h)/2:enable='lt(t,1.8)'"
    )


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
    # 자막을 하단 검은 띠 안에 배치 (MarginV는 하단에서의 거리 → 띠 중앙쯤).
    # 1080x1920 기준 스타일이므로 폰트도 키움.
    sub_margin = BANNER_BOTTOM_H // 3
    subtitle_style = (
        f"FontName={subtitle_font},FontSize=18,Bold=1,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,MarginV={sub_margin}"
    )
    filters = []
    if (tmp_path / "subs.srt").exists():
        filters.append(f"subtitles=subs.srt:force_style='{subtitle_style}'")

    # 오프닝 브랜딩 — 첫 1.8초 채널명 표시 (자막 타이밍 영향 없음, 전체 영상 t=0 기준)
    opening = _opening_filter()
    if opening:
        filters.append(opening)

    vf = ",".join(filters) if filters else "null"

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
