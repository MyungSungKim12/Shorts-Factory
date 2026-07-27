# 업로드 설명란 해시태그 및 서버 동기화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 영상 설명란에 주제 해시태그를 안정적으로 추가하고, 서버의 자막 수정 및 실행 설정을 로컬과 일치시킨다.

**Architecture:** 업로더에 순수 함수 `_description_with_hashtags(description, tags, max_hashtags=5, max_length=5000)`를 추가하고 실제 YouTube 요청 본문에서 사용한다. 서버의 자막 어절 줄바꿈은 관련 테스트와 함께 선별 병합하며, `.env`는 저장소 밖에 백업한 후 서버 파일을 로컬의 Git 무시 파일로 동기화한다.

**Tech Stack:** Python 3.12+, pytest, YouTube Data API v3, FFmpeg/libass, PowerShell, SSH/SCP

## Global Constraints

- 별도 AI/API 호출 없이 `script.json`의 `tags`만 사용한다.
- 주제 해시태그는 최대 5개이며 기존 설명과 기존 해시태그를 보존한다.
- 한국어 자막은 약 13자 폭에서 어절 단위로만 줄바꿈한다.
- `.env`, `credentials/`, API 키와 OAuth 토큰은 커밋하거나 출력하지 않는다.
- 업로드는 일 6건 절대 한도를 넘기지 않는다.
- 서버의 캐시·임시 파일·단순 주석 차이는 로컬에 병합하지 않는다.

---

### Task 1: 서버 자막 어절 줄바꿈 병합

**Files:**
- Modify: `tests/test_story_producer.py`
- Modify: `app/agents/story_producer.py`

**Interfaces:**
- Consumes: `_split_caption(text: str, max_len: int = 22) -> list[str]`
- Produces: `_wrap_caption(text: str, width: int = 13) -> str`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_wrap_caption_breaks_only_between_words():
    wrapped = story_producer._wrap_caption(
        "사하라의 눈 리차트 구조의 놀라운 비밀",
        width=13,
    )

    assert wrapped == "사하라의 눈 리차트\n구조의 놀라운 비밀"
    assert "리차\n트" not in wrapped
```

인트로 자막 검증은 명시적 개행을 허용하도록 다음처럼 변경한다.

```python
assert "Spoken story title" in subtitles.replace(chr(10), " ")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_story_producer.py::test_wrap_caption_breaks_only_between_words -q`

Expected: FAIL with `AttributeError: module ... has no attribute '_wrap_caption'`

- [ ] **Step 3: 최소 구현 추가**

```python
def _wrap_caption(text: str, width: int = 13) -> str:
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)
```

`_write_srt()`의 인트로·본문·CTA 자막에 `_highlight_caption(_wrap_caption(text))` 순서로 적용한다.

- [ ] **Step 4: 관련 테스트 통과 확인**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: PASS

### Task 2: 설명란 주제 해시태그 생성

**Files:**
- Create: `tests/test_uploader.py`
- Modify: `app/agents/uploader.py`

**Interfaces:**
- Consumes: `description: str`, `tags: Iterable[object]`
- Produces: `_description_with_hashtags(description, tags, max_hashtags=5, max_length=5000) -> str`

- [ ] **Step 1: 순수 함수 실패 테스트 작성**

```python
def test_description_appends_clean_unique_topic_hashtags():
    result = uploader._description_with_hashtags(
        "리차트 구조를 살펴봅니다. #지구미스터리",
        ["사하라의 눈", "#리차트-구조", "지구미스터리", "사하라의 눈"],
    )

    assert result == (
        "리차트 구조를 살펴봅니다. #지구미스터리\n\n"
        "#사하라의눈 #리차트구조"
    )


def test_description_limits_hashtags_to_five():
    result = uploader._description_with_hashtags(
        "설명",
        ["하나", "둘", "셋", "넷", "다섯", "여섯"],
    )

    assert result.endswith("#하나 #둘 #셋 #넷 #다섯")
    assert "#여섯" not in result


def test_description_without_valid_tags_is_unchanged():
    assert uploader._description_with_hashtags("원래 설명", ["#", "---", " "]) == "원래 설명"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_uploader.py -q`

Expected: FAIL with `AttributeError: module ... has no attribute '_description_with_hashtags'`

- [ ] **Step 3: 최소 구현 추가**

```python
def _description_with_hashtags(
    description: str,
    tags: Iterable[object],
    *,
    max_hashtags: int = 5,
    max_length: int = 5000,
) -> str:
    base = str(description or "").rstrip()
    existing = {tag.casefold() for tag in re.findall(r"#([\w가-힣]+)", base)}
    hashtags: list[str] = []
    seen = set(existing)
    for raw in tags or []:
        clean = re.sub(r"[^\w가-힣]", "", str(raw).lstrip("#"))
        key = clean.casefold()
        if not clean or key in seen:
            continue
        candidate = f"#{clean}"
        suffix = " ".join([*hashtags, candidate])
        combined = f"{base}\n\n{suffix}" if base else suffix
        if len(combined) > max_length:
            continue
        hashtags.append(candidate)
        seen.add(key)
        if len(hashtags) >= max_hashtags:
            break
    if not hashtags:
        return base
    suffix = " ".join(hashtags)
    return f"{base}\n\n{suffix}" if base else suffix
```

업로드 요청 본문에는 다음 값을 사용한다.

```python
"description": _description_with_hashtags(
    script.get("description", ""),
    script.get("tags", []),
),
```

- [ ] **Step 4: 실제 업로드 본문 경계 테스트 작성**

`run_uploader()`의 외부 YouTube 클라이언트와 품질 검증만 테스트 대역으로 바꾸고 캡처한 `body["snippet"]["description"]`이 해시태그가 포함된 값인지 검증한다.

```python
assert captured["body"]["snippet"]["description"] == (
    "사하라의 눈 설명\n\n#사하라의눈 #리차트구조"
)
```

- [ ] **Step 5: 업로더 테스트 통과 확인**

Run: `python -m pytest tests/test_uploader.py -q`

Expected: PASS

### Task 3: 서버 설정 동기화와 배포

**Files:**
- Replace ignored local file: `.env`
- Deploy: `app/agents/story_producer.py`
- Deploy: `app/agents/uploader.py`
- Deploy: `tests/test_story_producer.py`
- Deploy: `tests/test_uploader.py`

**Interfaces:**
- Consumes: 서버 `/home/ubuntu/shorts-factory-be/.env`
- Produces: 동일한 로컬 `.env` 및 동일한 배포 대상 소스

- [ ] **Step 1: 로컬 `.env`를 저장소 밖에 백업**

PowerShell에서 임시 디렉터리의 절대경로를 만든 뒤 기존 `.env`를 복사한다. 백업 경로는 저장소 내부에 만들지 않는다.

- [ ] **Step 2: 서버 `.env`를 로컬 `.env`로 안전하게 복사**

SCP로 서버 `.env`를 로컬 `.env`에 복사하고 `git check-ignore -v .env`로 계속 무시되는지 확인한다. 파일 내용은 터미널에 출력하지 않는다.

- [ ] **Step 3: 전체 로컬 테스트 실행**

Run: `python -m pytest -q`

Expected: PASS

- [ ] **Step 4: 소스와 테스트를 서버에 배포**

SCP로 네 대상 파일을 서버의 같은 상대 경로에 복사한다. `.env`는 이미 서버가 기준이므로 서버에 다시 올리지 않는다.

- [ ] **Step 5: 서버 핵심 검증 실행**

Run remotely:

```bash
cd /home/ubuntu/shorts-factory-be
venv/bin/python -m pytest tests/test_uploader.py tests/test_story_producer.py -q
venv/bin/python -m py_compile app/agents/uploader.py app/agents/story_producer.py
```

Expected: PASS and exit code 0

- [ ] **Step 6: 서버·로컬 대상 파일 동일성 확인**

네 대상 파일의 SHA-256을 양쪽에서 비교한다. `.env`는 해시가 같은지만 확인하고 내용은 출력하지 않는다.

### Task 4: 최종 Git 검증과 커밋

**Files:**
- Modify: `app/agents/producer.py`
- Modify: `app/agents/story_producer.py`
- Modify: `app/agents/uploader.py`
- Modify: `tests/test_story_producer.py`
- Create: `tests/test_uploader.py`
- Create: `docs/superpowers/plans/2026-07-27-upload-description-hashtags-and-server-sync.md`

**Interfaces:**
- Consumes: 전체 구현 및 테스트 결과
- Produces: 비밀정보를 제외한 재현 가능한 Git 커밋

- [ ] **Step 1: 비밀 파일 제외 확인**

Run:

```bash
git status --short
git check-ignore -v .env credentials/token.json credentials/client_secret.json
git diff --check
```

Expected: `.env`와 `credentials/`는 Git 변경 목록에 없음

- [ ] **Step 2: 전체 테스트 재실행**

Run: `python -m pytest -q`

Expected: PASS

- [ ] **Step 3: 소스·테스트·계획만 커밋**

```bash
git add app/agents/producer.py app/agents/story_producer.py app/agents/uploader.py tests/test_story_producer.py tests/test_uploader.py docs/superpowers/plans/2026-07-27-upload-description-hashtags-and-server-sync.md
git commit -m "기능: 주제 해시태그와 자막 줄바꿈 동기화"
```
