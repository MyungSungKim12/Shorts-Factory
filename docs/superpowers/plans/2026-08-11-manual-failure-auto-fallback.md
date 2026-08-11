# Manual Topic Failure Auto-Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수동 소재가 실제 예약 상태가 아니면 정시 사전 제작이 기존 자동 소재 경로로 계속 진행되게 한다.

**Architecture:** `manual_reservation_for_prebuild()`는 `reserved` 상태의 수동 예약만 반환한다. 검증 실패·추가 입력 필요·취소 등 제작 대상이 아닌 상태는 `None`으로 취급하여 기존 `_prepare()` 자동 경로를 사용한다.

**Tech Stack:** Python 3.12, SQLite, pytest

## Global Constraints

- 자동 제작·업로드 일정과 일 6건 업로드 한도는 변경하지 않는다.
- 수동 예약이 `reserved`인 경우에만 기존 수동 제작 경로를 유지한다.
- `.env`, `credentials/`는 수정하거나 커밋하지 않는다.

---

### Task 1: 실패 수동 검증의 자동 제작 복귀

**Files:**
- Modify: `app/services/slot_prebuild.py`
- Test: `tests/test_slot_prebuild.py`

**Interfaces:**
- Consumes: `manual_reservation_for_prebuild(data_dir: Path, run_id: str)`
- Produces: `reserved` 행은 기존 dict, 그 외 상태는 `None`

- [ ] **Step 1: 실패 상태 수동 기록이 `None`을 반환하는 테스트 작성**
- [ ] **Step 2: 테스트가 현재 `failed` dict 반환 때문에 실패하는지 확인**
- [ ] **Step 3: `reserved`가 아닌 상태를 자동 경로 대상으로 처리하는 최소 구현**
- [ ] **Step 4: 관련 테스트와 전체 백엔드 테스트 실행**
- [ ] **Step 5: 서버 배포 후 2~4회차 자동 사전 제작 경로 확인**

### Task 2: 자동 생성 소재 히스토리 API

**Files:**
- Modify: `app/routes/slots.py`
- Test: `tests/test_slot_api.py`

**Interfaces:**
- Consumes: `data/work/{run_id}/topic.json`, `script.json`, `videos.sqlite`
- Produces: 페이지 번호와 크기를 받는 읽기 전용 자동 생성 소재 히스토리 응답

- [ ] **Step 1: 자동 생성 회차만 최신순·페이징하는 API 실패 테스트 작성**
- [ ] **Step 2: 테스트가 엔드포인트 부재로 실패하는지 확인**
- [ ] **Step 3: 작업 산출물과 업로드 이력을 결합한 최소 읽기 API 구현**
- [ ] **Step 4: API 테스트와 백엔드 전체 테스트 실행**

### Task 3: 프론트 자동 소재 히스토리 화면

**Files:**
- Modify: `src/slotApi.js`
- Modify: `src/SlotManager.jsx`
- Modify: `src/App.css`
- Test: `src/slotApi.test.js`
- Test: `src/SlotManager.test.jsx`

**Interfaces:**
- Consumes: Task 2의 자동 생성 소재 히스토리 API
- Produces: 회차·소재·제목·상태·시간을 보여주는 페이징 목록

- [ ] **Step 1: API 계약과 화면 렌더링 실패 테스트 작성**
- [ ] **Step 2: 테스트가 클라이언트·화면 부재로 실패하는지 확인**
- [ ] **Step 3: 기존 회차 관리 화면 하단에 자동 소재 히스토리와 페이징 구현**
- [ ] **Step 4: 프론트 테스트와 프로덕션 빌드 실행**
- [ ] **Step 5: 백엔드·프론트 배포 후 실제 화면 확인**
