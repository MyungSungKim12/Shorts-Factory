# Free Retention Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추가 유료 서비스 없이 첫 화면, 쇼트 리듬, 모션, 자막 위치·강조, 무료 미디어 폴백, 제한적 전환음을 개선하고 검증된 로컬 sample 한 편을 만든다.

**Architecture:** 기존 story writer 계약과 FFmpeg 합성 파이프라인을 유지한다. `story_producer.py`에는 역할별 편집 계획과 렌더링만 추가하고, `media_library.py`에는 후보 선택·검증 폴백만 추가한다. 운영 서버는 sample 승인 전까지 변경하지 않는다.

**Tech Stack:** Python 3.12, pytest, Pillow, FFmpeg/ffprobe, SRT/libass, requests

## Global Constraints

- Gemini Omni, Veo 또는 유료 음원 API를 호출하지 않는다.
- 영상은 1080x1920, 60~75초를 유지한다.
- 상하단 검은 프레임과 상단 고정 제목을 유지한다.
- 자막 중심은 1920px 캔버스의 약 70% 높이에 둔다.
- 숫자·단위와 공백 없는 한국어 토큰을 중간에서 나누지 않는다.
- 효과음은 한 편당 최대 두 번, 80~140ms로 제한한다.
- `.env`, `credentials/`, `data/`, `.analysis_reference/`는 커밋하지 않는다.
- sample 승인 전 운영 서버, 서비스, 예약 실행을 변경하지 않는다.

---

### Task 1: 로컬 참고 자료를 Git 상태에서 제외

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: 로컬 비교 자료 디렉터리 `.analysis_reference/`
- Produces: 깨끗한 `git status`와 유지되는 로컬 참고 영상

- [ ] **Step 1: ignore 규칙 부재를 확인한다**

Run: `git check-ignore .analysis_reference/current_sheet.jpg`

Expected: exit 1

- [ ] **Step 2: 정확한 ignore 규칙을 추가한다**

```gitignore
.analysis_reference/
```

- [ ] **Step 3: 참고 파일은 남고 Git에서 제외되는지 확인한다**

Run: `git check-ignore .analysis_reference/current_sheet.jpg; Test-Path .analysis_reference/current_sheet.jpg`

Expected: ignore 경로 출력 후 `True`

- [ ] **Step 4: 커밋한다**

```powershell
git add .gitignore
git commit -m "chore: ignore local video references"
```

### Task 2: 역할별 잔존율 쇼트 계획과 짧은 인트로 문구

**Files:**
- Modify: `app/agents/story_producer.py:90-118`
- Modify: `app/agents/story_producer.py:495-550`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `scene: dict`, `duration: float`, `script["title"]`
- Produces: `_shot_duration_range(role: str) -> tuple[float, float]`, `_spoken_intro(title: str) -> str`, `_scene_shots(scene: dict, duration: float | None = None) -> list[dict]`

- [ ] **Step 1: 실패 테스트를 작성한다**

```python
def test_retention_shot_ranges_are_role_specific():
    assert story_producer._shot_duration_range("hook") == (1.8, 2.2)
    assert story_producer._shot_duration_range("context") == (2.4, 3.2)
    assert story_producer._shot_duration_range("mechanism") == (2.2, 3.0)
    assert story_producer._shot_duration_range("payoff") == (2.0, 2.8)
    assert story_producer._shot_duration_range("close") == (2.5, 3.5)


def test_spoken_intro_keeps_one_short_topic_phrase():
    assert story_producer._spoken_intro("300일 동안 번개가 멈추지 않는 마을의 비밀") == "300일 동안 번개가 멈추지 않는 마을"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_story_producer.py -k "retention_shot or spoken_intro" -q`

Expected: missing helper failures

- [ ] **Step 3: 역할별 범위와 인트로 축약을 구현한다**

```python
ROLE_SHOT_RANGES = {
    "hook": (1.8, 2.2),
    "context": (2.4, 3.2),
    "problem": (2.4, 3.2),
    "mechanism": (2.2, 3.0),
    "payoff": (2.0, 2.8),
    "close": (2.5, 3.5),
}


def _shot_duration_range(role: str) -> tuple[float, float]:
    return ROLE_SHOT_RANGES.get(role, (2.2, 3.0))


def _spoken_intro(title: str) -> str:
    normalized = re.sub(r"[?!。]+$", "", str(title)).strip()
    words = normalized.split()
    selected = []
    for word in words:
        if selected and len(" ".join(selected + [word])) > 22:
            break
        selected.append(word)
    return " ".join(selected) or normalized[:22]
```

`_scene_shots`는 역할 범위의 중간값으로 필요한 개수를 계산하고, 마지막 쇼트가 최소값보다 짧으면 앞 쇼트에 합친다. 인트로 TTS 입력은 전체 제목 대신 `_spoken_intro(script["title"])`를 사용한다.

- [ ] **Step 4: producer 테스트를 통과시킨다**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: all pass

- [ ] **Step 5: 커밋한다**

```powershell
git add app/agents/story_producer.py tests/test_story_producer.py
git commit -m "feat: pace story shots for retention"
```

### Task 3: 자막 상향 배치와 제한적 키워드 강조

**Files:**
- Modify: `app/agents/story_producer.py:289-437`
- Modify: `app/agents/story_producer.py:437-465`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: 자막 cue 텍스트
- Produces: `_highlight_caption(text: str) -> str`, `_subtitle_style(font: str) -> str`

- [ ] **Step 1: 실패 테스트를 작성한다**

```python
def test_subtitle_style_moves_caption_to_lower_middle():
    style = story_producer._subtitle_style("Malgun Gothic")
    assert "Alignment=2" in style
    assert "MarginV=500" in style


def test_caption_highlights_only_one_number_or_keyword():
    highlighted = story_producer._highlight_caption("무려 300일 동안 번개가 칩니다")
    assert highlighted.count(r"{\c&H00D7FF&}") == 1
    assert highlighted.count(r"{\c&HFFFFFF&}") == 1
    assert "300일" in highlighted
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_story_producer.py -k "lower_middle or highlights_only" -q`

Expected: margin assertion and missing helper failure

- [ ] **Step 3: 강조와 위치를 구현한다**

```python
HIGHLIGHT_PATTERN = re.compile(r"\d[\d,.]*(?:년|개월|일|시간|분|초|명|개|km|m|%|배)?|비밀|하지만|놀랍게도")


def _highlight_caption(text: str) -> str:
    match = HIGHLIGHT_PATTERN.search(text)
    if not match:
        return text
    return (
        text[:match.start()]
        + r"{\c&H00D7FF&}"
        + match.group(0)
        + r"{\c&HFFFFFF&}"
        + text[match.end():]
    )
```

`_write_srt`에서 각 cue의 chunk에 `_highlight_caption`을 적용한다. `_subtitle_style`은 `Alignment=2,MarginV=500`을 사용한다. 1330px 영상 영역이 y=260~1590이므로 자막 기준선 y=1420이 되어 영상 영역의 하단 중앙에 남는다.

- [ ] **Step 4: 줄바꿈 회귀와 스타일 테스트를 실행한다**

Run: `python -m pytest tests/test_story_producer.py -k "caption or subtitle" -q`

Expected: all pass

- [ ] **Step 5: 커밋한다**

```powershell
git add app/agents/story_producer.py tests/test_story_producer.py
git commit -m "feat: elevate and emphasize story captions"
```

### Task 4: 무료 영상에도 교차 모션 적용

**Files:**
- Modify: `app/agents/story_producer.py:122-159`
- Modify: `app/agents/story_producer.py:234-263`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `media_file`, `duration`, `motion_index`
- Produces: `visual_filter(..., motion_index: int = 0) -> str`

- [ ] **Step 1: 실패 테스트를 작성한다**

```python
def test_video_motion_alternates_by_shot_index():
    first = story_producer.visual_filter("shot.mp4", 2.5, motion_index=0)
    second = story_producer.visual_filter("shot.mp4", 2.5, motion_index=1)
    assert "scale=1124:1383" in first
    assert first != second
    assert "crop=1080:1330" in first
    assert "crop=1080:1330" in second
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_story_producer.py::test_video_motion_alternates_by_shot_index -q`

Expected: unexpected keyword argument failure

- [ ] **Step 3: 모션 인덱스 기반 필터를 구현한다**

비디오를 1.04배 확대한 뒤 짝수 쇼트는 왼쪽에서 오른쪽, 홀수 쇼트는 오른쪽에서 왼쪽으로 크롭 x 좌표를 이동한다. 이미지는 기존 zoompan의 x/y 식을 인덱스에 따라 중앙 확대, 좌우 패닝, 상하 패닝으로 순환한다. `_encode_visual_clip` 호출에 전역 `shot_n`을 전달한다.

- [ ] **Step 4: 모션 필터와 전체 producer 테스트를 실행한다**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: all pass

- [ ] **Step 5: 커밋한다**

```powershell
git add app/agents/story_producer.py tests/test_story_producer.py
git commit -m "feat: add alternating motion to story media"
```

### Task 5: 손상된 무료 미디어 후보 폴백

**Files:**
- Modify: `app/services/media_library.py:32-48`
- Modify: `app/services/media_library.py:205-274`
- Test: `tests/test_media_library.py`

**Interfaces:**
- Consumes: 후보 목록과 다운로드된 경로
- Produces: `choose_candidates(...) -> list[MediaCandidate]`, `fetch_story_media(...)`의 다음 후보 폴백

- [ ] **Step 1: 실패 테스트를 작성한다**

```python
def test_fetch_tries_next_candidate_after_invalid_download(tmp_path, monkeypatch):
    broken = candidate(1, 1080, 1920)
    valid = candidate(2, 1080, 1920)
    monkeypatch.setattr(media_library, "_pexels_video_candidates", lambda keyword: [broken, valid])
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_is_usable_download", lambda path: path.read_bytes() == b"valid")
    monkeypatch.setattr(
        media_library,
        "_download_candidate",
        lambda item, output: output.write_bytes(b"broken" if item.media_id == "1" else b"valid"),
    )
    path, metadata = asyncio.run(
        media_library.fetch_story_media(["storm"], tmp_path / "shot", set())
    )
    assert path.read_bytes() == b"valid"
    assert metadata["media_id"] == "2"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_media_library.py::test_fetch_tries_next_candidate_after_invalid_download -q`

Expected: missing validation or first candidate selected

- [ ] **Step 3: 순위 후보 반복과 검증을 구현한다**

`choose_candidates`는 미사용 후보를 `_quality` 내림차순으로 반환한다. `fetch_story_media`는 각 후보를 다운로드한 뒤 파일 존재, 1KB 초과 여부를 `_is_usable_download`로 검사하고 실패 파일을 삭제한 뒤 다음 후보를 시도한다. 기존 `choose_candidate`는 첫 원소를 반환해 호환성을 유지한다.

- [ ] **Step 4: media library 테스트를 실행한다**

Run: `python -m pytest tests/test_media_library.py -q`

Expected: all pass

- [ ] **Step 5: 커밋한다**

```powershell
git add app/services/media_library.py tests/test_media_library.py
git commit -m "fix: fall back from unusable free media"
```

### Task 6: 제한적 자체 생성 전환음

**Files:**
- Modify: `app/agents/story_producer.py:437-493`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `tmp_path`, `ffmpeg_path`, payoff 시작 시각
- Produces: `_create_transition_tone(path: Path, ffmpeg_path: str) -> bool`, 최대 두 번의 저음량 오디오 믹스

- [ ] **Step 1: 실패 테스트를 작성한다**

```python
def test_transition_events_are_limited_to_hook_and_payoff():
    script = {"scenes": [
        {"n": 1, "role": "hook"},
        {"n": 2, "role": "context"},
        {"n": 3, "role": "payoff"},
        {"n": 4, "role": "close"},
    ]}
    assert story_producer._transition_scene_numbers(script) == [1, 3]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_story_producer.py::test_transition_events_are_limited_to_hook_and_payoff -q`

Expected: missing helper failure

- [ ] **Step 3: 이벤트 선택과 전환음을 구현한다**

```python
def _transition_scene_numbers(script: dict) -> list[int]:
    selected = []
    for scene in script.get("scenes", []):
        if scene.get("role") in {"hook", "payoff"}:
            selected.append(int(scene["n"]))
        if len(selected) == 2:
            break
    return selected
```

`_create_transition_tone`은 FFmpeg lavfi `sine=frequency=520:duration=0.12`에 `afade`와 `volume=0.035`를 적용한다. 생성 실패 시 `False`를 반환하고 기존 오디오 합성을 계속한다. `_finish_video`는 성공한 tone만 payoff 시작 시각에 `adelay` 후 기존 `[aout]`과 `amix`한다.

- [ ] **Step 4: producer 테스트를 실행한다**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: all pass

- [ ] **Step 5: 커밋한다**

```powershell
git add app/agents/story_producer.py tests/test_story_producer.py
git commit -m "feat: add restrained story transition cues"
```

### Task 7: 전체 회귀 검증과 로컬 sample 생성

**Files:**
- Output only: `data/samples/retention-v1/`

**Interfaces:**
- Consumes: existing Google ADC, Pexels/Pixabay configuration, FFmpeg
- Produces: `topic.json`, `script.json`, `produce_log.json`, `validation.json`, `output.mp4`

- [ ] **Step 1: 전체 테스트를 실행한다**

Run: `python -m pytest -q`

Expected: all pass

- [ ] **Step 2: sample을 생성한다**

```powershell
$env:CONTENT_FORMAT='story'
$env:TTS_PROVIDER='google'
python scripts/generate_sample.py --sample-id retention-v1
```

Expected: `data/samples/retention-v1/output.mp4` 생성

- [ ] **Step 3: 검증 결과를 확인한다**

```powershell
Get-Content -Raw data/samples/retention-v1/validation.json
Get-Content -Raw data/samples/retention-v1/produce_log.json
```

Expected: validation `ok=true`, 1080x1920, 60~75초, 오디오 존재, 검은 화면 기준 통과

- [ ] **Step 4: 실제 프레임과 오디오를 점검한다**

첫 화면, 본문 자막 위치, 숫자 강조, payoff 전환 장면, CTA를 각각 캡처해 확인한다. 오디오 파형과 최종 길이를 ffprobe로 확인한다.

- [ ] **Step 5: sample 위치와 검증 결과를 사용자에게 전달한다**

운영 서버 배포와 실제 업로드는 사용자의 sample 승인 후 별도 단계로 수행한다.
