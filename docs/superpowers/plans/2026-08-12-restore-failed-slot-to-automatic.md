# Restore Failed Slot To Automatic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수동 실패 회차를 화면에서 기존 자동 소재 제작·업로드 경로로 안전하게 복구한다.

**Architecture:** 예약 서비스가 수동 게이트를 원자적으로 제거하고 라우트가 현재 시각에 따라 예약 대기, 명시 회차 사전제작, 즉시 전체 파이프라인 중 하나를 예약한다. 프론트는 가능한 상태에서만 보호된 자동 전환 API를 호출한다.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, React, Vite, Node test runner

## Global Constraints

- 기존 하루 4회 자동 스케줄과 YouTube 일 6건 한도를 변경하지 않는다.
- 활성 작업자나 이미 검토·승인·업로드 단계인 회차는 자동 전환하지 않는다.
- `.env`와 `credentials/`는 수정하거나 커밋하지 않는다.

---

### Task 1: 예약 게이트 자동 복구

**Files:**
- Modify: `app/services/slot_reservations.py`
- Test: `tests/test_slot_reservations.py`

**Interfaces:**
- Produces: `restore_automatic_slot(data_dir: Path, run_id: str, now: datetime) -> dict`

- [ ] 실패·추가입력 상태가 자동 카드로 복구되고 이벤트는 남는 실패 테스트를 작성한다.
- [ ] 활성 worker와 제작 완료 상태를 거부하는 실패 테스트를 작성한다.
- [ ] 테스트가 기대한 이유로 실패하는지 실행한다.
- [ ] 수동 예약 행을 조건부 삭제하는 최소 구현을 추가한다.
- [ ] 대상 테스트를 통과시킨다.

### Task 2: 시각별 자동 실행 API

**Files:**
- Modify: `app/routes/slots.py`
- Test: `tests/test_slot_api.py`

**Interfaces:**
- Consumes: 기존 `prepare_slot(data_dir, ffmpeg_path, slot) -> dict`
- Produces: `POST /api/slots/{run_id}/automatic`

- [ ] 제작 전, 제작 후·업로드 전, 업로드 후 세 분기의 실패 테스트를 작성한다.
- [ ] 기존 명시 회차 `prepare_slot`과 전체 `run_pipeline`을 시각별로 선택하는 보호된 API를 구현한다.
- [ ] 대상 테스트를 통과시킨다.

### Task 3: 프론트 자동 소재 전환

**Files:**
- Modify: `D:/ms/shorts-factory-fe/src/slotApi.js`
- Modify: `D:/ms/shorts-factory-fe/src/slotState.js`
- Modify: `D:/ms/shorts-factory-fe/src/SlotCard.jsx`
- Test: `D:/ms/shorts-factory-fe/src/slotState.test.js`

**Interfaces:**
- Consumes: `POST /api/slots/{run_id}/automatic`
- Produces: `canRestoreAutomatic`, `client.restoreAutomatic(runId)`

- [ ] 가능한 상태와 API 계약의 실패 테스트를 작성하고 실패를 확인한다.
- [ ] 버튼·확인 문구·호출 구현을 추가한다.
- [ ] 프론트 테스트와 빌드를 통과시킨다.

### Task 4: 통합 검증과 배포

**Files:**
- Modify: `docs/OPERATIONS.md`

- [ ] 운영 문서에 자동 전환 시각별 동작을 기록한다.
- [ ] 백엔드 전체 pytest와 프론트 전체 테스트·빌드를 실행한다.
- [ ] 두 저장소를 한글 메시지로 커밋·푸시한다.
- [ ] 서버에 동일 소스를 배포하고 서비스·API·프론트 반영을 확인한다.
