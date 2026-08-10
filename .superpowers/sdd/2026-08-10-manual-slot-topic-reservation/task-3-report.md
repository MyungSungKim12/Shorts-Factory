# Task 3 구현 보고서 — 수동 예약 소재 사전제작과 영속 이벤트

## 상태

- Task 3 구현 완료
- 수동 예약 분기는 기존 자동 `_prepare`의 전역 잠금 진입 전에 결정된다.
- 수동 worker가 `data/recovery/pipeline.lock`을 직접 단독 소유하고, 성공·실패 모두 소유권을 확인해 해제한다.
- 집중 회귀와 전체 백엔드 테스트를 통과했다.

## 구현 내용

### 수동 예약 해석과 자동 경로 보존

- `manual_reservation_for_prebuild(data_dir, run_id)`를 추가했다.
- 기존 `videos.sqlite`에 수동 예약 테이블이 없거나 해당 회차의 `reserved` 수동 예약이 없으면 `None`을 반환한다.
- 예약이 있으면 `status=reservable`인 저장된 `check_result.topic_payload`만 읽어 `run_id`, 상태, 시도 번호, 제작·업로드 시각과 함께 반환한다.
- 예약 레코드가 있으나 검증 payload가 손상된 경우 자동 소재로 조용히 대체하지 않고 명시적으로 실패한다.
- `prepare_slot()`은 target을 한 번만 계산한 뒤 수동 예약을 먼저 조회한다. 수동 예약이면 `run_manual_prebuild()`로 바로 분기하고, 없으면 계산된 target을 기존 `_prepare()`에 전달한다.
- target 사전 전달로 기존 자동 경로의 `now_fn` 호출 횟수, researcher → writer → producer 순서, 반환 dict 형식을 유지했다.

### 수동 prebuild worker

- `run_manual_prebuild()`는 다음 순서로 실행한다.
  1. 저장된 수동 예약과 시도 번호 확인
  2. `data/recovery/pipeline.lock` 원자적 획득
  3. `lock_reserved_slot()`으로 회차 worker 원자적 선점
  4. `staging/manual-prebuild-<run_id>-<attempt>` 신규 생성
  5. 저장된 검증 `topic_payload`를 변경 없이 `topic.json`으로 기록
  6. 기존 writer, producer, quality gate, staging promotion 경계 호출
  7. 승격 후 `manual_review.json` 기록
  8. 예약 상태를 `review_ready`로 전이하고 worker 해제
- producer 경계는 기본 async producer와 동기 테스트 double을 모두 지원한다.
- 실행 중 `PIPELINE_RUN_ID`를 해당 회차로 설정하고 성공·실패 후 이전 환경값을 복원한다.
- `manual_review.json`에는 `run_id`, `attempt`, `state=review_ready`, 실제 승격된 `topic.json` bytes의 `topic_sha256`을 기록한다.
- 성공 반환값에는 기존 prebuild 소비자가 사용하는 `run_id`, `scheduled_at`, `destination`, `quality_gate`와 수동 상태·시도 번호를 포함한다.

### 상태와 영속 이벤트

- 경계 진입 전 상태를 `researching → writing → producing → quality_check`로 전이한다.
- 각 전이 뒤 사용자용 한국어 진행 이벤트를 append-only `slot_events`에 기록한다.
- 성공 시 `review_ready` 이벤트를 기록하고 `artifact_path`를 승격된 `work/<run_id>`로 저장한다.
- 실패 시 원래 예외를 그대로 다시 발생시키면서 현재 수동 단계를 `failed` 상태와 `stage`에 기록하고 `worker_id=NULL`로 해제한다.
- 실패 이벤트 메타데이터는 `attempt`와 allowlisted `failed_stage`만 포함한다. provider 원문, raw payload, 예외 메시지, 토큰은 이벤트에 기록하지 않는다.
- 전역 잠금은 모든 종료 경로의 `finally`에서 소유자 ID와 PID가 일치할 때만 해제한다.

### checked topic writer 계약 적응

- Task 2의 수동 소재 계약은 채널 밖 실제 카테고리(예: `economy`)도 경고와 함께 허용하지만 기존 자동 writer는 자동 StoryTopic 카테고리만 재검증했다.
- checked `topic_payload`를 권위 있는 입력으로 재사용하기 위해 `run_writer(..., manual_checked=False)` 선택 인자를 좁게 추가했다.
- 자동 호출은 기본값으로 기존 `validate_topic()`을 그대로 사용한다.
- 수동 worker만 `manual_checked=True`를 전달해 Task 2의 `validate_manual_story_topic()` 계약으로 방어적 재검증한다.
- writer 프롬프트, 생성·재시도·파일 출력 동작은 변경하지 않았다.

## TDD RED/GREEN 증거

### RED 1 — 수동 worker/라우팅 부재

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py
```

결과: 수집 단계에서 `ImportError: cannot import name 'manual_slot_pipeline' from 'app.services'`로 실패했다. 새 worker 모듈과 수동 라우팅이 없는 정확한 이유의 RED였다.

### RED 2 — checked 수동 소재 writer 계약 부재

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_story_prompts.py::test_writer_uses_manual_contract_for_prechecked_topic
```

결과: `TypeError: run_writer() got an unexpected keyword argument 'manual_checked'`로 실패했다. 자동 계약을 변경하지 않고 수동 검증 계약을 선택할 인터페이스가 없음을 재현했다.

### GREEN 1 — worker, 라우팅, writer 계약

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py tests/test_story_prompts.py::test_writer_uses_manual_contract_for_prechecked_topic
```

결과: `32 passed in 0.74s`.

### GREEN 2 — 집중 회귀

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py tests/test_recovery.py tests/test_quality_gate.py tests/test_slot_reservations.py tests/test_manual_topic.py tests/test_story_contracts.py tests/test_story_prompts.py
```

결과: `144 passed in 2.48s`.

### 전체 백엔드 검증

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q
```

결과: `361 passed in 3.28s`.

추가 검사:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m compileall -q app scripts tests\test_manual_slot_pipeline.py tests\test_slot_prebuild.py tests\test_story_prompts.py
git diff --check
```

결과: compileall 성공, diff 오류 없음(CRLF 변환 안내만 존재).

## 변경 파일

- `app/services/manual_slot_pipeline.py` — 수동 worker, 잠금, 단계 전이, 안전 이벤트, review marker
- `app/services/slot_prebuild.py` — reserved manual payload resolver
- `scripts/prepare_next_slot.py` — explicit slot의 pre-lock 수동/자동 라우팅과 target 재사용
- `app/agents/writer.py` — 자동 기본 동작을 유지하는 checked-manual 검증 선택자
- `tests/test_manual_slot_pipeline.py` — 실제 SQLite/승격 기반 성공·실패·이벤트·잠금 테스트
- `tests/test_slot_prebuild.py` — 수동 분기가 자동 전역 락보다 먼저 실행되는 회귀
- `tests/test_story_prompts.py` — 수동 checked topic의 writer 계약 선택 회귀
- `.superpowers/sdd/2026-08-10-manual-slot-topic-reservation/task-3-report.md` — 본 보고서

## 셀프 리뷰

- 자동 경로: 기존 자동 test의 researcher → writer → producer 순서와 결과 형식이 유지됨을 확인했다.
- 잠금: 수동 경로는 자동 `_prepare`에 진입하지 않으며 worker 내부에서만 전역 잠금을 획득·해제한다.
- 경쟁 상태: 수동 예약 선점은 Task 1의 `BEGIN IMMEDIATE` 기반 `lock_reserved_slot()`을 사용한다.
- 입력 권위: 자동 researcher를 호출하거나 checked topic을 재작성하지 않는다. 작성된 `topic.json`과 marker hash가 일치한다.
- 실패 복구: producer 예외를 재현해 `state=failed`, 실패 단계 유지, `worker_id=NULL`, 전역 잠금 제거를 확인했다.
- 이벤트 안전: 공급자 비밀이 포함된 예외를 재현해 저장 이벤트 어디에도 비밀 문자열이 남지 않음을 확인했다.
- 승격: 기존 `promote_staging()`을 실제 사용해 필수 산출물·script hash·quality gate 검사를 우회하지 않는다.
- 범위: `.env`, `credentials/`, 업로드 로직, 관련 없는 리팩터링은 변경하지 않았다.

## 우려 및 후속 통합 메모

- `run_manual_prebuild()`는 현재 공개 인터페이스 계획대로 잠금 대기 옵션이 없으며, 다른 살아 있는 pipeline worker가 전역 잠금을 보유하면 즉시 실패 상태가 아닌 호출 오류로 반환한다. 예약 worker 선점 전이므로 `reserved` 상태와 `worker_id=NULL`은 유지된다. 재시도 정책은 Task 4의 명시적 retry 동작에서 결정할 수 있다.
- 승격 이후 `manual_review.json` 쓰기 또는 최종 SQLite 전이가 실패하면 완성된 `work/<run_id>`는 보존되지만 예약은 `failed`가 될 수 있다. 데이터 손실보다 복구 가능 보존을 택한 동작이며 Task 4의 재시도/폐기 로직은 기존 artifact 존재 여부를 확인해야 한다.
- 수동 writer 선택자는 기본값이 `False`라 기존 모든 자동 호출과 테스트에는 영향이 없다. 향후 다른 수동 호출자가 writer를 직접 사용할 때만 명시적으로 `manual_checked=True`를 전달해야 한다.

---

## 게이트 리뷰 수정 라운드 1

### 기준과 상태

- 수정 기준 커밋: `c552c58 기능: 예약 소재 회차 영상 사전제작`
- IMPORTANT 2건을 재현 테스트로 확인한 뒤 수정했다.
- 자동 `prepare_slot` 라우팅과 자동 researcher/writer/producer 결과 계약은 변경하지 않았다.

### 원인 분석

1. 기존 worker는 `promote_staging()`이 staging을 제거한 뒤 `manual_review.json`을 쓰고 `review_ready` DB 전이를 수행했다. marker 또는 DB 전이가 실패하면 완성본이 `work/<run_id>`에 남아 이후 `ensure_target_available()`이 재시도를 차단했다.
2. 기존 예외 정리는 failed 상태 전이와 실패 이벤트 append를 한 broad `try`에 넣고 모든 정리 예외를 삼켰다. 상태 전이가 실패하면 active 상태와 `worker_id`가 남은 채 global lock만 해제될 수 있었고, 이벤트 실패도 관찰할 수 없었다.

### 수정 내용

#### 승격 이후 복구 가능성

- `manual_review.json`을 quality gate 통과 후 staging에 먼저 기록한다. 따라서 marker 쓰기 실패는 promotion 전에 발생하며 `work/<run_id>`를 만들지 않는다.
- marker가 포함된 staging만 기존 `promote_staging()` 경계로 승격한다.
- promotion 이후 `review_ready` DB 전이가 실패하면 완성 패키지를 삭제하지 않고 `data/recovery/manual-artifacts/manual-prebuild-<run_id>-<attempt>-failed-.../`로 같은 파일시스템에서 원자 이동한다.
- 실패 예약의 `artifact_path`에는 복구 보관 경로를 기록한다.
- 원래 `work/<run_id>`가 비워지므로 `ensure_target_available()`이 통과하고 다음 attempt promotion을 막지 않는다.
- archive 이동까지 실패한 비정상 상황은 원래 예외에 비밀 없는 note를 추가하고 global lock을 유지해, active worker/artifact 충돌 상태에서 다른 pipeline이 진입하지 않게 한다.

#### 실패 상태와 worker 정리

- 정상 `transition_slot(..., target="failed")`을 최대 2회 시도한다.
- 두 번 모두 실패하면 새 `fail_owned_slot()` fallback이 `BEGIN IMMEDIATE` 안에서 active 상태와 정확한 `worker_id` 소유권을 확인하고 `state=failed`, `worker_id=NULL`, 실패 단계와 선택적 archive 경로를 원자 저장한다.
- fallback은 이미 `failed`이고 worker가 없는 상태에 대해 idempotent하다.
- 정상 전이와 fallback이 모두 실패하면 `data/recovery/manual-cleanup/<run_id>-<attempt>.json`에 raw 오류 없이 cleanup 필요 상태를 기록하고 global lock을 해제하지 않는다.
- 실패 이벤트 append는 상태 정리와 별도 경계로 실행한다. 이벤트 저장 실패는 상태 정리를 되돌리거나 가리지 않으며 원래 pipeline 예외에 `수동 제작 실패 이벤트 기록에 실패했습니다`라는 bounded note만 추가한다.
- provider 예외·이벤트 저장 예외의 원문과 raw payload는 DB 이벤트, cleanup report, exception note에 기록하지 않는다.

### 수정 라운드 TDD 증거

#### RED — post-promotion 및 cleanup 실패 재현

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py
```

수정 전 결과: `5 failed, 3 passed in 0.86s`.

- promotion 진입 시 staging에 `manual_review.json`이 없어 실패
- marker 실패 후 `work/<run_id>`가 남아 실패
- `review_ready` 전이 실패 후 승격 artifact가 `work/<run_id>`에 남아 실패
- failed 전이 주입 시 상태가 `producing`, worker가 설정된 채 남아 실패
- 실패 이벤트 저장 오류가 원래 예외에 안전하게 보고되지 않아 실패

#### GREEN — 수동 worker 회귀

같은 명령 결과: `8 passed in 0.79s`.

#### GREEN — 관련 집중 회귀

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py tests/test_slot_reservations.py tests/test_recovery.py tests/test_quality_gate.py tests/test_story_prompts.py
```

결과: `101 passed in 2.47s`.

추가 검사:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m compileall -q app tests\test_manual_slot_pipeline.py
git diff --check
```

결과: compileall 성공, diff 오류 없음(CRLF 변환 안내만 존재).

#### 전체 백엔드

명령:

```powershell
D:\ms\shorts-factory-be\venv\Scripts\python.exe -m pytest -q
```

결과: `366 passed in 3.93s`.

### 수정 라운드 셀프 리뷰와 우려

- archive에는 `topic.json`, `script.json`, `produce_log.json`, `output.mp4`, `prepared.json`, `manual_review.json`을 포함한 완성 패키지가 보존된다.
- marker 실패와 DB 전이 실패 테스트 모두 worker 해제, global lock 해제, `work/<run_id>` 부재와 target 재사용 가능성을 확인한다.
- 일반 failed 전이 오류 테스트는 ownership fallback으로 상태와 worker가 정리된 뒤에만 global lock이 해제됨을 확인한다.
- 이벤트 저장 오류 테스트는 원래 render 예외를 유지하고 DB 상태 cleanup이 먼저 완료되며 provider 비밀 문자열이 note에 포함되지 않음을 확인한다.
- 파일시스템 자체가 archive 이동을 거부하거나 SQLite 정상 전이와 ownership fallback이 모두 실패한 경우에는 global lock을 의도적으로 유지한다. 호출 프로세스 종료 후 기존 stale-lock 회수 경로가 잠금을 정리할 수 있으며, cleanup report가 운영자 복구 단서를 남긴다.
