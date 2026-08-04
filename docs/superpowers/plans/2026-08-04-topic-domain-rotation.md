# Topic Domain Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최근 실제 소재 중복을 차단하고 과학·숨겨진 세계 안에서 여덟 하위 영역을 이틀 단위로 순환한다.

**Architecture:** `researcher.py`가 DB에서 제목과 소재를 함께 읽고, run_id 날짜·회차로 결정한 하위 영역을 기존 프롬프트 컨텍스트에 추가한다. 모델 호출과 캐시 흐름은 유지하며 선택 방향만 제한한다.

**Tech Stack:** Python 3.12, SQLite, pytest

## Global Constraints

- 하루 4회와 모델 호출 횟수를 변경하지 않는다.
- `science_mystery`, `hidden_world` 상위 카테고리를 유지한다.
- 하루 네 영역은 중복되지 않고 이틀 동안 여덟 영역을 한 번씩 사용한다.
- 최근 14일의 제목과 실제 소재를 모두 중복 제외 목록에 넣는다.

---

### Task 1: 최근 실제 소재 중복 차단

**Files:**
- Modify: `app/agents/researcher.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: `videos(title, topic, date)`
- Produces: `_load_recent_topics(data_dir: Path, days: int = 14) -> list[str]`

- [x] 제목과 실제 소재가 다른 업로드 행을 만드는 실패 테스트를 작성한다.
- [x] `python -m pytest tests/test_story_prompts.py -q`로 실패를 확인한다.
- [x] SQL이 `title, topic`을 조회하고 빈 값과 중복을 제거한 목록을 반환하도록 구현한다.
- [x] 대상 테스트가 통과하는지 확인한다.

### Task 2: 날짜·회차 기반 하위 영역 순환

**Files:**
- Modify: `app/agents/researcher.py`
- Test: `tests/test_cache_and_slots.py`

**Interfaces:**
- Produces: `story_focus_domain(run_id: str) -> dict[str, str] | None`

- [x] 같은 날 네 회차의 영역이 모두 다르고 이틀 동안 여덟 영역이 모두 선택되는 실패 테스트를 작성한다.
- [x] 대상 테스트 실패를 확인한다.
- [x] 과학 4개, 숨겨진 세계 4개 영역과 날짜 홀짝 오프셋을 구현한다.
- [x] 대상 테스트 통과를 확인한다.

### Task 3: 리서처 프롬프트에 영역과 의미 중복 규칙 반영

**Files:**
- Modify: `app/agents/researcher.py`
- Modify: `agents/01_trend-researcher.md`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: `context['focus_domain']`, `context['recent_topics']`
- Produces: 선택 영역 이름·설명·예시와 핵심 대상/사건/관측값 중복 금지를 포함한 프롬프트

- [x] 프롬프트 내용 실패 테스트를 작성하고 실패를 확인한다.
- [x] 컨텍스트와 프롬프트 블록을 최소 수정한다.
- [x] 에이전트 운영 문서를 현재 시간순 회차와 영역 순환 기준으로 갱신한다.
- [x] 대상 테스트를 통과시킨다.

### Task 4: 전체 검증·커밋·서버 배포

**Files:**
- Modify: 위 파일과 설계·계획 문서

- [x] `python -m pytest -q`와 `git diff --check`를 실행한다.
- [ ] 의도한 파일만 커밋하고 main에 푸시한다.
- [ ] 커밋 아카이브를 서버에 배포하고 서버 전체 테스트를 실행한다.
- [ ] 대시보드, 크론, 15시 회차 미제작 상태 또는 새 로직 적용 상태를 확인한다.
