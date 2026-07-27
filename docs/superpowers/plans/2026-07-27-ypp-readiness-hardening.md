# YPP Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2회차부터 AI 공개 표시, Wikimedia 크레딧, 시각 관련성, 고유 해석과 서사 변형을 적용한다.

**Architecture:** 제작 로그를 업로드 메타데이터의 단일 근거로 사용하고, 시각 검색 경계와 작가 프롬프트를 좁게 보완한다. 기존 생성·복구·예약·업로드 흐름은 유지한다.

**Tech Stack:** Python 3.12, pytest, YouTube Data API v3, Pexels/Pixabay/Wikimedia, Linux cron

## Global Constraints

- 기존 1회차 산출물은 변경하지 않는다.
- 하루 4회와 9개 cron, 외부 API 호출 횟수는 변경하지 않는다.
- `.env`, `credentials/`, `data/`는 커밋하거나 배포로 덮어쓰지 않는다.
- 서버 배포 전에 소스와 cron을 백업한다.

---

### Task 1: 업로드 공개 정보

**Files:**
- Modify: `app/agents/uploader.py`
- Test: `tests/test_uploader.py`

**Interfaces:**
- Consumes: `produce_log.json`의 `intro.ai_generation`, `sources`
- Produces: YouTube `status.containsSyntheticMedia`, 설명란 Wikimedia 크레딧

- [ ] 실패 테스트로 Veo 사용 여부와 크레딧 형식을 고정한다.
- [ ] 테스트 실패가 기능 부재 때문인지 확인한다.
- [ ] 제작 로그 기반 업로드 메타데이터를 구현한다.
- [ ] 업로더 테스트를 통과시킨다.

### Task 2: 스톡 관련성 경계

**Files:**
- Modify: `app/services/visual_relevance.py`
- Modify: `app/services/media_library.py`
- Test: `tests/test_visual_relevance.py`
- Test: `tests/test_media_library.py`

**Interfaces:**
- Consumes: 검증된 `visual_identity`, 공급자 후보 설명·URL
- Produces: 대상 앵커가 포함된 검색어와 최소 어휘 관련성을 통과한 후보

- [ ] 무관 후보 배제와 대상 앵커 유지 실패 테스트를 작성한다.
- [ ] 실패를 확인하고 최소 필터를 구현한다.
- [ ] 관련 후보와 정확 Wikimedia 경로가 유지되는지 검증한다.

### Task 3: 채널 고유 대본

**Files:**
- Modify: `app/agents/writer.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: 검증된 `topic.json`
- Produces: 소재별 서사 패턴과 검증 사실 기반 채널 해석 지침

- [ ] 프롬프트 계약 실패 테스트를 작성하고 실패를 확인한다.
- [ ] 결정적 네 가지 패턴과 해석 문장 규칙을 추가한다.
- [ ] 기존 대본 계약 테스트를 통과시킨다.

### Task 4: 배포와 운영 확인

**Files:**
- Deploy: `app/`, `tests/`, `agents/`, `docs/`
- Preserve: server `.env`, `credentials/`, `data/`, crontab

**Interfaces:**
- Consumes: 로컬 검증 완료 소스
- Produces: 로컬과 동일한 서버 소스 및 2회차부터 적용되는 운영 상태

- [ ] 로컬 전체 pytest와 컴파일을 실행한다.
- [ ] 서버 소스·cron을 타임스탬프 백업한다.
- [ ] 추적 소스만 전송하고 서버 전체 pytest·컴파일을 실행한다.
- [ ] dashboard, health, cron 9개, 1회차 보존과 2회차 미생성 상태를 확인한다.
