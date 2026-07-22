# 하루 4회 운영 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 세 회차를 보존하면서 14시 미스터리 회차를 추가하고 오늘 즉시 업로드한다.

**Architecture:** 중앙 `SCHEDULE`에 slot 4를 시간순으로 추가하고 사전 제작 CLI와 cron이 같은 번호를 사용한다. 기존 잠금·복구·품질 게이트·업로더는 변경하지 않는다.

**Tech Stack:** Python 3.12, pytest, Linux cron, SSH, SQLite, YouTube Data API

## Global Constraints

- `.env`와 `credentials/`는 커밋하거나 출력하지 않는다.
- YouTube 업로드는 일 6건을 넘기지 않는다.
- 14시 회차는 slot 4이며 기존 slot 2·3의 의미를 바꾸지 않는다.
- 서버 변경 전 소스·설정·자격 증명·데이터·cron을 백업한다.

---

### Task 1: 네 번째 예약 회차

**Files:**
- Modify: `tests/test_slot_prebuild.py`
- Modify: `app/services/slot_prebuild.py`
- Modify: `scripts/prepare_next_slot.py`
- Modify: `scripts/run_daily.py`

**Interfaces:**
- Consumes: `SCHEDULE`, `next_scheduled_slot()`, `scheduled_run()`
- Produces: slot 4=14:00 KST 선택과 `--slot 4` CLI

- [ ] 13시 이후 다음 회차가 `YYYYMMDD-4`인지 확인하는 실패 테스트를 작성한다.
- [ ] 해당 테스트가 기존 17시 회차를 반환해 실패하는지 실행한다.
- [ ] `SCHEDULE`에 `(4, time(14, 0))`을 추가하고 CLI 선택지를 4까지 확장한다.
- [ ] 대상 테스트와 전체 테스트를 실행한다.

### Task 2: 운영 배포와 오늘 즉시 실행

**Files:**
- Modify: `docs/OPERATIONS.md`
- Server: `/home/ubuntu/shorts-factory-be`

**Interfaces:**
- Consumes: 검증된 로컬 소스와 기존 서버 상태
- Produces: cron 9개와 오늘 `YYYYMMDD-4` 업로드 기록

- [ ] 운영 문서에 12시 사전 제작·14시 업로드를 기록한다.
- [ ] 서버 백업을 만들고 추적된 소스만 배포한다.
- [ ] 원격 테스트·컴파일·서비스 상태를 검증한다.
- [ ] cron을 9개로 교체하고 `run_scheduled.py 4`를 즉시 실행한다.
- [ ] YouTube URL, SQLite 한 건, 로그 성공, 잠금 해제를 확인한다.

