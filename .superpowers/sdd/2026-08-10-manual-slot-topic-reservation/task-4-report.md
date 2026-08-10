# Task 4 보고서 — 승인 게이트, 반려, 재시도, 예약 업로드

## 결과

- 수동 예약이 없으면 기존 자동 파이프라인으로 진행한다.
- 미승인 수동 회차는 리서처·작가·프로듀서·업로더를 호출하지 않고 성공한 보류 로그를 남긴다.
- 업로드 시각 이후에만 `approved → uploading`을 SQLite `BEGIN IMMEDIATE`로 단일 클레임한다.
- 승인된 수동 패키지는 기존 산출물만 검증·재사용하며 업로드 결과를 `uploaded` 또는 `failed`로 확정한다.
- 반려 시 라이브 작업자와 전역 락 해제를 확인하고 `data/rejected/<run_id>-attempt-<N>`으로 보존한 뒤 활성 경로를 비운다.
- 같은 소재 재시도는 유효한 마지막 검증 결과가 있을 때만 `reserved`, 새 소재 재시도는 검증 필드를 비우고 `checking`으로 전이한다.
- 스케줄러가 기본 7일 보존 기간으로 반려 산출물만 정리하며 활성 work 및 AI 라이브러리는 건드리지 않는다.

## RED

1. 신규 서비스가 없는 상태에서 다음 명령을 실행했다.

   `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_actions.py tests/test_uploader.py tests/test_recovery.py tests/test_temp_cleanup.py`

   결과: 수집 오류 2건, `ModuleNotFoundError: app.services.manual_slot_actions`.

2. 승인 시각 경계 테스트를 추가하고 다음 명령을 실행했다.

   `D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_actions.py -k atomically`

   결과: 1 failed. 업로드 시각 1분 전에도 `approved`를 반환해 조기 업로드를 클레임하는 결함을 재현했다.

## GREEN 및 검증

- 집중 검증: `python -m pytest -q tests/test_manual_slot_actions.py tests/test_uploader.py tests/test_recovery.py tests/test_temp_cleanup.py`
- 전체 회귀: `python -m pytest -q`
- 정적 확인: `git diff --check`
- 구문 확인: `python -m compileall -q app scripts`

최종 실행 결과는 커밋 직전 재검증 결과를 기준으로 한다.

## 변경 파일

- `app/services/manual_slot_actions.py`
- `app/agents/orchestrator.py`
- `scripts/run_scheduled.py`
- `app/services/temp_cleanup.py`
- `tests/test_manual_slot_actions.py`
- `tests/test_uploader.py`
- `tests/test_recovery.py`
- `tests/test_temp_cleanup.py`

## 자체 검토

- 자동 회차는 예약 DB나 수동 행이 없으면 `automatic`을 반환하며 work 파일을 선행 생성하지 않는다.
- 보류 분기는 네 콘텐츠 에이전트 호출 전에 반환한다.
- 일 업로드 한도는 환경값이 더 커도 6건으로 제한되는 회귀 테스트를 유지한다.
- 폐기 정리는 정규식에 맞는 `data/rejected` 직속 실제 디렉터리만 대상으로 하고 심볼릭 링크를 제외한다.
- `.env`, `credentials/`, 프론트엔드 및 무관 파일은 변경하지 않았다.

## 남은 연계 사항

- 업로드 시각 이후 승인 응답의 `upload_action=immediate`를 실제 단일 백그라운드 `run_pipeline` 호출로 연결하는 API 작업은 계획대로 Task 5 범위다.
