"""영상 프로듀서 에이전트 — output.mp4 생성 (간단 버전)."""
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

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

        # 2. 비디오 다운로드 (검색 실패 시 카테고리 안전어로 폴백)
        print("  → 비디오 다운로드 중...")
        fallback_kw = _category_fallback(date_str)
        video_files, dl_meta = await _download_videos(script, tmp_path, fallback_kw)

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
        #    첫 씬(훅)은 전체화면 + 큰 반전 문구 (순위 리스트 없음 → 자동템플릿 인상 완화, 시청 시작 유도).
        #    2번째 씬부터 상단 타이틀 띠 + 좌측 누적 순위 리스트.
        print("  → 씬별 영상 생성 중...")
        scene_videos = []
        hook_idx = 0  # 첫 씬을 훅으로 취급
        for idx, scene in enumerate(scenes):
            scene_n = scene["n"]
            mp3_file = mp3_files.get(scene_n)
            video_file = video_files.get(scene_n)

            if not (mp3_file and video_file):
                continue

            scene_video = tmp_path / f"enc_{scene_n}.mp4"
            is_hook = (idx == hook_idx)

            if is_hook and fontfile:
                # 전체화면 훅: 순위 리스트 없이 큰 반전 문구만
                hook_vf = _build_hook_overlay(scene.get("narration", ""), fontfile)
                _encode_scene_video(
                    str(video_file), str(mp3_file), str(scene_video), ffmpeg_path,
                    extra_vf=hook_vf, fullscreen=True,
                )
            else:
                revealed = {r for r, at in reveal_at.items() if at <= idx}
                extra_vf = ""
                if items_map and fontfile:
                    extra_vf = _build_scene_overlay(
                        topic_title, items_map, ranking_size, revealed,
                        scene.get("rank"), fontfile,
                    )
                _encode_scene_video(
                    str(video_file), str(mp3_file), str(scene_video), ffmpeg_path,
                    extra_vf=extra_vf,
                )
            scene_videos.append((scene_n, scene_video))

        if not scene_videos:
            raise RuntimeError("인코딩된 씬이 없습니다")

        # 4. 자막 생성 — 훅 씬은 큰 문구로 이미 표시되므로 하단 자막에서 제외
        print("  → 자막 생성 중...")
        hook_scene_n = scene_videos[0][0] if scene_videos else None
        _build_srt(script, scene_videos, ffmpeg_path, tmp_path / "subs.srt",
                   skip_scenes={hook_scene_n} if hook_scene_n else set())

        # 5. 씬들을 연결 + 자막 굽기
        print("  → 씬 연결 및 자막 합성 중...")
        output_mp4 = work_dir / "output.mp4"
        _concat_videos(scene_videos, str(output_mp4), ffmpeg_path, tmp_path)

        # 6. 실제 완성 길이 측정 + 계획 대비 기록. 숏츠 상한(180초) 초과만 중단.
        #    60초를 살짝 넘겨도 정상 숏츠이므로 업로드한다 (영상을 날리지 않음).
        actual_dur = _media_duration(str(output_mp4), ffmpeg_path)
        planned_dur = float(script.get("total_duration_sec", 0))
        max_sec = int(os.getenv("MAX_VIDEO_SEC", "180"))
        if actual_dur > max_sec:
            raise RuntimeError(
                f"완성 영상 {actual_dur:.1f}초 > 숏츠 상한 {max_sec}초 — 중단. "
                f"(계획 {planned_dur:.0f}초, TTS가 비정상적으로 김 → 대본 축약 필요)"
            )
        if actual_dur > 60:
            print(f"  · 완성 {actual_dur:.0f}초 (목표 초과지만 숏츠 범위 내 → 업로드 진행)")

        # 7. 제작 로그 (실제 길이·계획 길이·씬별 소스·대본 해시)
        script_hash = _sha256(script_file.read_bytes())
        produce_log = {
            "date": date_str,
            "timestamp": datetime.now().isoformat(),
            "output_file": str(output_mp4),
            "scenes_processed": len(scene_videos),
            "planned_duration": planned_dur,
            "actual_duration": round(actual_dur, 1),
            "duration_gap": round(actual_dur - planned_dur, 1),
            "script_sha256": script_hash,
            "sources": [
                {"scene": n, **dl_meta.get(n, {})} for n, _ in scene_videos
            ],
            "fallback_scenes": sum(1 for m in dl_meta.values() if m.get("fallback")),
            # 실험 태그 — 첫화면/훅 개편 전후 성과 비교용 (분석 시 참고)
            "experiment": os.getenv("EXPERIMENT_TAG", "hook_v2_fullscreen"),
        }
        # 계획과 실제가 10초 이상 벌어지면 경고 (대본 길이 산정 개선 신호)
        if abs(actual_dur - planned_dur) > 10:
            print(f"  ⚠️ 계획({planned_dur:.0f}s)-실제({actual_dur:.0f}s) 차이 큼 — 대본 길이 산정 확인")

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


def _category_fallback(run_id: str) -> str:
    """run_id('20260716-1')의 회차로 카테고리 안전 검색어 반환. 없으면 일반어."""
    from app.agents.researcher import SLOT_CATEGORIES
    if "-" in run_id:
        try:
            slot = int(run_id.rsplit("-", 1)[1])
            return SLOT_CATEGORIES.get(slot, {}).get("visual_fallback", "abstract background")
        except ValueError:
            pass
    return "abstract background"


def _clean_visual_keyword(kw: str) -> str:
    """서술형 검색어를 스톡 친화적으로 축약 (앞 3단어만, 부사·수식 제거).

    예: 'afghan hound running with long silky hair blowing in the wind' → 'afghan hound running'
    긴 문장은 스톡에서 0건 → 폴백을 유발하므로 핵심 명사구만 남긴다.
    """
    words = kw.split()
    return " ".join(words[:3]) if len(words) > 3 else kw


async def _download_videos(script: dict, tmp_path: Path, fallback_kw: str = "abstract background") -> dict:
    """각 씬별로 비디오 확보. 씬 검색어 실패 시 카테고리 안전어(fallback_kw)로 재시도.

    후보 검색어: [축약한 씬 visual, 카테고리 안전어] 순.
    소스: 각 검색어에 대해 Pexels 비디오 → Pixabay 비디오 → Pexels 이미지.
    전부 실패 시에만 검은 배경 (첫 단어 재검색 같은 위험한 일반화는 하지 않음).
    """
    video_files = {}
    have_pixabay = bool(os.getenv("PIXABAY_API_KEY"))

    async def try_fetch(keyword, video_path, image_path):
        """(경로, 제공자) 반환. 실패 시 (None, None)."""
        try:
            await download_video(keyword, str(video_path))
            return video_path, "pexels_video"
        except Exception:
            pass
        if have_pixabay:
            try:
                await download_video_pixabay(keyword, str(video_path))
                return video_path, "pixabay_video"
            except Exception:
                pass
        try:
            await download_image(keyword, str(image_path))
            return image_path, "pexels_image"
        except Exception:
            return None, None

    meta = {}  # scene_n → {primary, used, provider, fallback}
    for scene in script.get("scenes", []):
        scene_n = scene["n"]
        video_file = tmp_path / f"scene_{scene_n}.mp4"
        image_file = tmp_path / f"scene_{scene_n}.jpg"

        primary = _clean_visual_keyword(scene.get("visual", "") or "")
        candidates = [k for k in dict.fromkeys([primary, fallback_kw]) if k]  # 순서 유지 중복 제거

        result = provider = used = None
        for kw in candidates:
            result, provider = await try_fetch(kw, video_file, image_file)
            if result:
                used = kw
                break

        if result:
            video_files[scene_n] = result
            meta[scene_n] = {"primary": primary, "used": used,
                             "provider": provider, "fallback": used != primary}
            if used != primary:
                print(f"  · 씬 {scene_n}: '{primary}' 없음 → 안전어 '{fallback_kw}'로 확보")
        else:
            _create_black_bg(image_file)
            video_files[scene_n] = image_file
            meta[scene_n] = {"primary": primary, "used": None,
                             "provider": "black_bg", "fallback": True}
            print(f"  ⚠️ 씬 {scene_n}: 소스 없음 → 검은 배경")

    return video_files, meta


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


def _build_srt(script: dict, scene_videos: list, ffmpeg_path: str, srt_path: Path,
               skip_scenes: set = None) -> None:
    """씬별 실제 길이 측정 + 나레이션을 문장 단위로 분할해 순차 표시되는 SRT 생성.

    skip_scenes에 든 씬은 하단 자막을 넣지 않는다(시간은 진행) — 훅 씬은 큰 문구로 이미 표시되므로.
    """
    skip_scenes = skip_scenes or set()
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
        text = "" if scene_n in skip_scenes else narrations.get(scene_n, "")
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


def _build_hook_overlay(hook_text: str, fontfile: str) -> str:
    """훅 씬용 전체화면 큰 문구 오버레이. 순위 리스트 없이 반전 문구만 화면 중앙에 크게.

    시청 시작(Appeal)을 높이려는 첫 화면 — 자동템플릿 인상을 주는 띠·리스트를 첫 씬엔 안 쓴다.
    """
    ff = fontfile.replace(":", "\\:")

    def esc(t: str) -> str:
        return t.replace("\\", "").replace("'", "’")

    # 훅 문장 전체를 표시하되, 길이에 따라 폰트·줄폭을 자동 조절 (문장 잘림 방지).
    # 짧으면 크게, 길면 폭을 넓히고 폰트를 줄여서 3줄짜리 하드컷으로 문장이 끊기는 사고를 없앤다.
    for width, fontsize, line_h in [(11, 88, 118), (14, 72, 98), (17, 60, 82), (20, 50, 70)]:
        lines = _wrap_text(hook_text, width)
        if len(lines) <= 4:
            break
    else:
        lines = _wrap_text(hook_text, 20)  # 그래도 길면 그대로(잘리지 않게 전부 표시)

    block_h = len(lines) * line_h
    top = max(60, (1920 - block_h) // 2 - 100)  # 화면 중앙보다 살짝 위, 최소 여백 확보
    filters = []
    for i, line in enumerate(lines):
        filters.append(
            f"drawtext=fontfile='{ff}':text='{esc(line)}':fontsize={fontsize}:fontcolor=white"
            f":borderw=7:bordercolor=black:box=1:boxcolor=black@0.45:boxborderw=20"
            f":x=(w-text_w)/2:y={top + i * line_h}"
        )
    return "," + ",".join(filters)


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

    def short_name(t: str, limit: int = 12) -> str:
        # 괄호 부연(예: "(이집트)") 제거 → 리스트를 짧고 깔끔하게
        t = _re.sub(r'\s*[\(（].*?[\)）]', '', t).strip()
        # 말줄임표는 주아체 글리프 없어 깨지므로 ".." 사용
        if len(t) > limit:
            t = t[:limit] + ".."
        return t

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
            name = short_name(name)
            filters.append(
                f"drawtext=fontfile='{ff}':text='{esc(name)}':fontsize={size}"
                f":fontcolor=white:borderw=6:bordercolor=black:x=150:y={y}"
            )

    return "," + ",".join(filters)


def _encode_scene_video(media_file: str, mp3_file: str, output_mp4: str, ffmpeg_path: str,
                        extra_vf: str = "", fullscreen: bool = False) -> None:
    """한 씬을 비디오/이미지 + 음성으로 mp4 인코딩. extra_vf로 오버레이 필터 추가.

    fullscreen=True면 상·하단 띠 없이 화면을 꽉 채운다 (훅 씬용).
    """
    # 미디어 파일이 .mp4면 비디오, .jpg면 이미지
    is_video = media_file.lower().endswith('.mp4')

    # 나레이션 배속 (1.0=원속, 1.2=20% 빠르게 — 숏츠 속도감)
    tts_speed = os.getenv("TTS_SPEED", "1.2")

    # 모든 씬을 동일 규격(1080x1920/30fps/44.1kHz)으로 통일 — concat 싱크 어긋남 방지
    if extra_vf and not fullscreen:
        # 오버레이가 있으면: 영상을 상·하단 검은 띠 사이 영역에 채우고 위아래를 검게 패딩
        vid_h = 1920 - BANNER_H - BANNER_BOTTOM_H
        normalize_vf = (
            f"scale=1080:{vid_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{vid_h},pad=1080:1920:0:{BANNER_H}:black,fps=30"
        )
        normalize_vf += extra_vf
    else:
        # 훅 씬(fullscreen) 또는 오버레이 없음: 화면 꽉 채움. 훅이면 그 위에 큰 문구.
        normalize_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"
        if extra_vf and fullscreen:
            normalize_vf += extra_vf

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
