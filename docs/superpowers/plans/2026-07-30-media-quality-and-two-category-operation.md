# 영상 품질 방어와 상위 2개 소재군 운영 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 제목·설명·밝기·음량 회귀를 이중 방어하고 하루 4회 슬롯을 상위 2개 소재군에 각각 2회 배치한다.

**Architecture:** 모델 산출물은 Pydantic 계약과 업로더 경계에서 검사하고, 미디어 품질은 제작 단계의 정규화와 업로드 직전 품질 게이트에서 재검사한다. 업로드 당시 실제 카테고리를 SQLite에 저장해 분석 결과가 나중의 슬롯 설정에 의해 바뀌지 않게 한다.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, FFmpeg/ffprobe, pytest

## Global Constraints

- `.env`와 `credentials/`는 수정하거나 커밋하지 않는다.
- YouTube 업로드 한도는 하루 6건을 넘기지 않는다.
- 외부 TTS, Veo, 스톡 API를 테스트에서 호출하지 않는다.
- 기존 11시·14시·17시·21시 스케줄과 최근 14일 중복 제외는 유지한다.
- 완성 영상의 고정 검은 상하단은 밝기 표본에서 제외한다.

---

### Task 1: 제목 지시문 유출 차단

**Files:**
- Modify: `app/models.py:10-12,199-224`
- Modify: `app/agents/writer.py:158-217`
- Modify: `app/agents/uploader.py:164-171`
- Test: `tests/test_story_contracts.py`
- Test: `tests/test_story_prompts.py`
- Test: `tests/test_uploader.py`

**Interfaces:**
- Produces: `validate_public_title(value: object) -> str`
- Consumes: `StoryScriptContract.title`, `run_uploader()`의 `script["title"]`

- [ ] **Step 1: 실패 테스트 작성**

```python
@pytest.mark.parametrize("title", [
    "100자 이하 제목: 지하 수정 동굴의 비밀",
    "제목: 지하 수정 동굴의 비밀",
    "글자 수 50자 이내 지하 수정 동굴의 비밀",
])
def test_story_title_rejects_prompt_instruction_leak(title):
    with pytest.raises(ValueError, match="제목 지시문"):
        validate_script(story_script(title=title), "story")
```

프롬프트 테스트는 `"title": "100자 이하 제목"`이 없고 실제 제목 예시가 있는지 확인한다. 업로더 테스트는 오래된 `script.json`에 지시문 제목을 넣었을 때 YouTube 클라이언트를 만들기 전에 `ValueError`가 발생하는지 확인한다.

- [ ] **Step 2: RED 확인**

Run: `pytest tests/test_story_contracts.py tests/test_story_prompts.py tests/test_uploader.py -q`

Expected: 지시문 제목이 현재 허용되거나 프롬프트 문자열이 남아 있어 FAIL.

- [ ] **Step 3: 최소 구현**

```python
_TITLE_INSTRUCTION_PATTERNS = (
    re.compile(r"^\s*\d+\s*자\s*(?:이하|이내)\s*제목\s*[:：]?"),
    re.compile(r"^\s*제목\s*[:：]"),
    re.compile(r"^\s*글자\s*수\s*\d+\s*자\s*(?:이하|이내)?"),
)

def validate_public_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if any(pattern.search(title) for pattern in _TITLE_INSTRUCTION_PATTERNS):
        raise ValueError("제목 지시문이 실제 제목에 포함됨")
    return title
```

두 Script 계약의 title validator와 업로더에서 같은 함수를 사용한다. 작가 JSON 예시는 `"10분만 머물러도 위험한 지하 수정 동굴의 비밀"`로 교체한다.

- [ ] **Step 4: GREEN 확인**

Run: `pytest tests/test_story_contracts.py tests/test_story_prompts.py tests/test_uploader.py -q`

Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/models.py app/agents/writer.py app/agents/uploader.py tests/test_story_contracts.py tests/test_story_prompts.py tests/test_uploader.py
git commit -m "수정: 제목 지시문 노출 이중 차단"
```

### Task 2: 공개 출처명 정리

**Files:**
- Modify: `app/agents/uploader.py:66-98`
- Test: `tests/test_uploader.py`

**Interfaces:**
- Produces: `_public_wikimedia_title(media_id: object) -> str`
- Consumes: `_description_with_wikimedia_credits()`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_wikimedia_credit_hides_internal_image_filename():
    result = uploader._description_with_wikimedia_credits("설명", [{
        "provider": "wikimedia_image",
        "media_id": "File:Nan Madol 11.jpg",
        "attribution": "Example Author",
        "license": "CC BY-SA 4.0",
        "source_url": "https://commons.wikimedia.org/wiki/File:Nan_Madol_11.jpg",
    }])
    assert "File:" not in result
    assert ".jpg" not in result
    assert "Nan Madol 11" in result
```

- [ ] **Step 2: RED 확인**

Run: `pytest tests/test_uploader.py::test_wikimedia_credit_hides_internal_image_filename -q`

Expected: `.jpg`가 남아 FAIL.

- [ ] **Step 3: 최소 구현**

```python
def _public_wikimedia_title(media_id: object) -> str:
    title = re.sub(r"^File:\s*", "", str(media_id or ""), flags=re.I)
    title = re.sub(r"\.(?:jpe?g|png|webp|gif|tiff?)$", "", title, flags=re.I).strip()
    return title or "Wikimedia Commons 이미지 자료"
```

원본 `produce_log.json`은 변경하지 않고 공개 설명 조립에만 적용한다.

- [ ] **Step 4: GREEN 확인**

Run: `pytest tests/test_uploader.py -q`

Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/agents/uploader.py tests/test_uploader.py
git commit -m "수정: 설명란 이미지 파일명 노출 제거"
```

### Task 3: 나레이션 음량 정규화와 최종 검사

**Files:**
- Modify: `app/agents/story_producer.py:319-339,1056-1122`
- Modify: `app/services/media_probe.py:46-102,129-145`
- Test: `tests/test_story_producer.py`
- Test: `tests/test_media_probe.py`

**Interfaces:**
- Produces: `_normalize_narration(source: Path, ffmpeg_path: str) -> None`
- Produces: `probe_video()` 결과의 `integrated_loudness_lufs`, `loudness_range_lu`, `true_peak_dbfs`
- Consumes: 제목·씬·CTA의 무음 제거 WAV

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_normalize_narration_targets_consistent_shortform_loudness(tmp_path, monkeypatch):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"wav")
    seen = {}
    monkeypatch.setattr(story_producer, "run_checked", lambda cmd, **kwargs: seen.setdefault("cmd", cmd))
    story_producer._normalize_narration(source, "ffmpeg")
    audio_filter = seen["cmd"][seen["cmd"].index("-filter:a") + 1]
    assert "loudnorm=I=-16:LRA=7:TP=-1.5" in audio_filter
```

`probe_video` 테스트는 FFmpeg stderr의 ebur128 Summary fixture를 파싱해 세 필드가 반환되는지, `validate_sample`이 LRA 10 LU 초과를 `audio_loudness_range`로 거부하는지 확인한다.

- [ ] **Step 2: RED 확인**

Run: `pytest tests/test_story_producer.py tests/test_media_probe.py -q`

Expected: 정규화 함수와 음량 필드가 없어 FAIL.

- [ ] **Step 3: 최소 구현**

`_trim_narration()` 직후 각 WAV에 다음 필터를 적용한다.

```python
f"loudnorm=I={target}:LRA=7:TP=-1.5"
```

기본 목표는 환경 변수 `NARRATION_TARGET_LUFS=-16`이며 유효 범위 `-24~-12` 밖이면 `-16`을 사용한다. 감속 처리 뒤에는 `alimiter=limit=0.841395`를 추가한다. `probe_video()`의 기존 black/silence 분석 명령에 `ebur128=peak=true`를 추가하고 Summary를 파싱한다.

- [ ] **Step 4: GREEN 확인**

Run: `pytest tests/test_story_producer.py tests/test_media_probe.py -q`

Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/agents/story_producer.py app/services/media_probe.py tests/test_story_producer.py tests/test_media_probe.py
git commit -m "수정: 나레이션 음량 균일화와 범위 검사"
```

### Task 4: 본문 영역 저휘도 검사와 제한 보정

**Files:**
- Modify: `app/services/media_probe.py:46-102,129-145`
- Modify: `app/agents/story_producer.py:600-632`
- Test: `tests/test_media_probe.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `probe_video()` 결과의 `dark_content_ratio`, `max_dark_content_seconds`
- Consumes: 고정 레이아웃 본문 영역 `crop=1080:1330:0:260`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_validate_sample_rejects_long_dark_content():
    report = valid_report(
        dark_content_ratio=0.35,
        max_dark_content_seconds=8.0,
    )
    assert "dark_content" in validate_sample(report)

def test_validate_sample_allows_short_dark_transition():
    report = valid_report(
        dark_content_ratio=0.04,
        max_dark_content_seconds=2.0,
    )
    assert "dark_content" not in validate_sample(report)
```

스토리 프로듀서 테스트는 어두운 우주 검색어의 샷 필터에 무조건 보정이 붙는 것이 아니라, 측정 결과가 기준 미달일 때만 제한된 `eq=brightness=0.06:gamma=1.15`가 붙는지 확인한다.

- [ ] **Step 2: RED 확인**

Run: `pytest tests/test_media_probe.py tests/test_story_producer.py -q`

Expected: 저휘도 필드와 판정이 없어 FAIL.

- [ ] **Step 3: 최소 구현**

완성 영상 검사 시 다음 표본 필터를 별도 실행한다.

```text
crop=1080:1330:0:260,fps=1,signalstats,metadata=print:key=lavfi.signalstats.YAVG
```

`YAVG < 40`인 표본을 저휘도로 정의하고, 비율이 25% 초과하거나 연속 6초 이상이면 실패한다. 제작 샷은 짧은 샷의 평균 YAVG가 40 미만일 때만 제한 보정을 적용하며 보정 후 재측정한다.

- [ ] **Step 4: GREEN 확인**

Run: `pytest tests/test_media_probe.py tests/test_story_producer.py -q`

Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/services/media_probe.py app/agents/story_producer.py tests/test_media_probe.py tests/test_story_producer.py
git commit -m "수정: 본문 영상 저휘도 탐지와 제한 보정"
```

### Task 5: 두 소재군 슬롯과 실제 카테고리 저장

**Files:**
- Modify: `app/agents/researcher.py:26-56`
- Modify: `app/agents/uploader.py:230-275`
- Modify: `app/agents/analyst.py:38-119`
- Modify: `agents/01_trend-researcher.md`
- Modify: `agents/05_analyst.md`
- Test: `tests/test_cache_and_slots.py`
- Test: `tests/test_uploader.py`
- Test: `tests/test_monitor_api.py`

**Interfaces:**
- Produces: SQLite `videos.category TEXT`
- Consumes: `topic.json["category"]`
- Produces: 분석 표시명 `_category_name(category: str | None) -> str`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_four_slots_use_two_winning_categories_twice_each():
    assert [SLOT_CATEGORIES[n]["category"] for n in (1, 4, 2, 3)] == [
        "science_mystery", "hidden_world", "science_mystery", "hidden_world",
    ]
```

업로더 테스트는 `_init_db()`가 기존 테이블에 category 열을 추가하고 업로드 INSERT에 `topic.json["category"]`를 저장하는지 확인한다. 분석 테스트는 저장된 `category`를 사용하며 `None`은 `legacy_unclassified`로 분리하는지 확인한다.

- [ ] **Step 2: RED 확인**

Run: `pytest tests/test_cache_and_slots.py tests/test_uploader.py tests/test_monitor_api.py -q`

Expected: 네 카테고리 매핑과 DB category 부재로 FAIL.

- [ ] **Step 3: 최소 구현**

```python
SLOT_CATEGORIES = {
    1: SCIENCE_MYSTERY,
    4: HIDDEN_WORLD,
    2: SCIENCE_MYSTERY,
    3: HIDDEN_WORLD,
}
```

공유 dict가 수정되지 않도록 각 슬롯에 복사본을 둔다. `_init_db()`에서 category 열을 추가하고 INSERT에 실제 topic category를 포함한다. 분석 SELECT는 category를 읽고 현재 슬롯 매핑을 사용하지 않는다.

- [ ] **Step 4: GREEN 확인**

Run: `pytest tests/test_cache_and_slots.py tests/test_uploader.py tests/test_monitor_api.py -q`

Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/agents/researcher.py app/agents/uploader.py app/agents/analyst.py agents/01_trend-researcher.md agents/05_analyst.md tests/test_cache_and_slots.py tests/test_uploader.py tests/test_monitor_api.py
git commit -m "기능: 상위 두 소재군과 실제 카테고리 분석 적용"
```

### Task 6: 전체 검증과 서버 배포

**Files:**
- Modify: `docs/OPERATIONS.md`
- Verify: all changed files

**Interfaces:**
- Consumes: Tasks 1~5의 모든 계약과 품질 보고서 필드
- Produces: 서버의 다음 사전 제작 회차부터 적용되는 배포본

- [ ] **Step 1: 문서 갱신**

운영 문서에 두 소재군 시간표, 제목 지시문 차단, 공개 출처 정리, 밝기·음량 실패 기준을 기록한다.

- [ ] **Step 2: 로컬 전체 검증**

Run: `pytest -q`

Expected: 모든 테스트 PASS.

Run: `python -m compileall app scripts`

Expected: exit code 0.

Run: `git diff --check`

Expected: 출력 없음.

- [ ] **Step 3: 운영 설정 보존 확인**

Run: `git status --short`

Expected: `.env`, `credentials/`, `data/`가 변경 목록에 없음.

- [ ] **Step 4: 배포**

서버 저장소를 타임스탬프 백업한 뒤 코드·테스트·문서를 전송한다. `.env`, `credentials/`, `data/`는 제외한다. 서비스를 재시작하고 cron 설정은 변경하지 않는다.

- [ ] **Step 5: 서버 검증**

Run on server:

```bash
cd /home/ubuntu/shorts-factory-be
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall app scripts
```

Expected: 모든 테스트 PASS, compileall exit code 0.

- [ ] **Step 6: 배포 커밋**

```bash
git add docs/OPERATIONS.md
git commit -m "문서: 영상 품질 방어 운영 기준 갱신"
```
