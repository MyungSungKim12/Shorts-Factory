# Task 5 작업 보고서

## 상태

- FastAPI `/api/slots` 목록, 소재 검사, 예약/취소, 상세, 이벤트, 영상, 승인, 반려, 재시도, 건너뛰기 API를 구현했다.
- 모든 변경 API와 영상 다운로드에 공용 `DASHBOARD_TOKEN` 의존성을 적용했고 기존 수동 파이프라인 실행 API도 같은 의존성을 사용한다.
- DB에 기록된 `data/work/<run_id>` 산출물만 영상으로 제공하며 경로 이탈과 누락은 404로 차단한다.

## RED

- 명령: `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py tests/test_monitor_api.py`
- 최초 결과: `17 failed, 11 passed`; 모든 신규 API가 404이거나 `app.routes`가 없어 실패함을 확인했다.
- 추가 회귀 RED:
  - 잘못된 달력 날짜가 서버 예외로 전파됨.
  - 새 소재 재시도의 저장 요청 검증 실패 후 상태가 `checking`으로 남음.
  - 누락 상세 회차가 자동 카드 200으로 반환됨.
  - 상세 응답에 회차 번호가 빠짐.

## GREEN

- 집중 명령: `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py tests/test_monitor_api.py`
- 최종 집중 결과: `33 passed in 0.82s`.
- 전체 명령: `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q`
- 전체 결과: `416 passed in 6.52s`.
- 보조 확인: `python -m compileall -q app tests/test_slot_api.py tests/test_monitor_api.py`, `git diff --check` 통과.

## 변경 파일

- `app/routes/__init__.py`
- `app/routes/slots.py`
- `app/main.py`
- `tests/test_slot_api.py`
- `tests/test_monitor_api.py`
- `.superpowers/sdd/2026-08-10-manual-slot-topic-reservation/task-5-report.md`

## Self-review 및 우려 사항

- 응답은 내부 `artifact_path`, 저장 원문 요청, 미정제 제공자 응답 및 자격 증명 키를 제외하도록 허용 목록으로 구성했다.
- 즉시 승인만 정확히 한 개의 `run_pipeline(..., slot=N)` 백그라운드 작업을 추가하며, 사전 승인은 cron을 기다린다.
- 새 소재 재시도는 저장된 요청을 먼저 검증한 뒤 상태를 바꾸고 검사 작업을 하나만 추가한다.
- 현재 확인된 미해결 결함은 없다.

## Gate review fix round 1

### RED

- `DATA_DIR/work`가 외부 디렉터리 심볼릭 링크일 때 상세 메타데이터와 MP4가 노출됨을 재현했다.
- 정상 artifact 디렉터리 안의 `script.json`이 외부 파일 링크일 때 공개 상세 API가 파일을 읽는 문제를 재현했다.
- 이벤트 조회가 임의 키, `access_token`, raw payload 및 메시지의 자격 증명 값을 그대로 반환하는 문제를 재현했다.
- `grounding_error`에 저장된 임의 제공자 오류 문자열이 상세 응답에 노출되는 문제를 재현했다.
- RED 명령: `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py -k "symlinked_work_root or symlinked_review_metadata or explicit_schema or non_allowlisted_grounding_error"`
- RED 결과: `4 failed, 22 deselected`.

### GREEN

- 해석된 `DATA_DIR` 내부의 비링크 `work/<run_id>`만 artifact 루트로 인정하고 detail/video가 같은 검증 함수를 사용하도록 했다.
- 리뷰 JSON과 MP4는 검증된 artifact 디렉터리의 직접 자식인 비링크 정규 파일만 읽는다.
- 이벤트 응답을 7개 고정 필드로 직렬화하고, 메시지를 500자로 제한·재마스킹하며 메타데이터 키/타입/길이를 허용 목록으로 제한했다.
- `grounding_error`는 `verification_method`, `verified_at`, `distinct_sources`, `fact_source_linkage`, `topic_contract` 코드만 공개한다.
- 집중 명령 결과: `37 passed in 0.91s`.
- 전체 명령 결과: `420 passed in 6.97s`.
- 보조 확인: `compileall`과 `git diff --check` 통과.

### 우려 사항

- 현재 확인된 미해결 결함은 없다.

## Gate review fix round 2

### RED

- `grounding_error`가 dict 또는 list일 때 공개 상세 직렬화의 set membership에서 `TypeError`가 발생함을 재현했다.
- 알 수 없는 제공자 오류 문자열도 함께 회귀 입력으로 고정했다.
- RED 명령: `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py::test_detail_omits_non_allowlisted_grounding_error`
- RED 결과: dict/list 2건 실패, unknown provider text 1건 통과.

### GREEN

- `grounding_error`가 문자열인지 먼저 검사한 뒤 공개 코드 허용 목록과 비교하고, 비문자열 또는 미등록 문자열은 모두 생략한다.
- 집중 명령 결과: `39 passed in 1.23s`.
- 전체 명령 결과: `422 passed in 5.40s`.
- 보조 확인: `compileall`과 `git diff --check` 통과.

### 우려 사항

- 현재 확인된 미해결 결함은 없다.
