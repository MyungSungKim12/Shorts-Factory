# Manual Slot Topic Reservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the existing four-slot automatic pipeline while allowing a user to reserve a topic for one slot, watch its production logs and final MP4 in the dashboard, and approve, reject, or retry that slot safely.

**Architecture:** Store one authoritative reservation and append-only event stream per `YYYYMMDD-slot` in the existing SQLite database. A small resolver at prebuild and upload boundaries selects the unchanged automatic path when no manual reservation exists, or the manual state machine when one exists. FastAPI exposes authenticated mutation/video endpoints, and the existing React dashboard polls slot/event endpoints every two seconds while an active manual job is visible.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLite, existing async agent pipeline and FFmpeg services, React 18, Vite 6, Node test runner.

## Global Constraints

- A slot without a manual reservation must execute the current automatic generation and upload path without changed behavior.
- New or edited reservations are accepted only before the slot's production time: 09:00, 12:00, 15:00, and 19:00 KST for slots 1–4.
- Manual videos require explicit approval; an unapproved slot is held and is never replaced by an automatic topic.
- Approval before 11:00, 14:00, 17:00, or 21:00 KST waits for that upload time; approval after it triggers one immediate upload.
- A rejected or failed manual slot may accept a replacement topic only after its previous worker and global pipeline lock are released.
- Channel-outside topics are allowed with a warning, but verification, safety, and visual-relevance contracts remain mandatory.
- Every mutating API and protected video download requires `DASHBOARD_TOKEN`; responses and events must never expose credentials or raw provider responses.
- YouTube uploads remain below the project limit of six per day.
- `.env` and `credentials/` must never be committed.

---

### Task 1: Reservation Persistence and State Machine

**Files:**
- Create: `app/services/slot_reservations.py`
- Create: `tests/test_slot_reservations.py`

**Interfaces:**
- Produces: `init_slot_tables(data_dir: Path) -> None`
- Produces: `slot_window(run_id: str) -> SlotWindow`
- Produces: `list_slot_cards(data_dir: Path, day: date, now: datetime) -> list[dict]`
- Produces: `create_check(data_dir: Path, run_id: str, request: dict, now: datetime) -> dict`
- Produces: `save_check_result(data_dir: Path, run_id: str, result: dict, now: datetime) -> dict`
- Produces: `reserve_checked_topic(data_dir: Path, run_id: str, now: datetime) -> dict`
- Produces: `lock_reserved_slot(data_dir: Path, run_id: str, worker_id: str, now: datetime) -> dict | None`
- Produces: `transition_slot(data_dir: Path, run_id: str, expected: set[str], target: str, now: datetime, **fields) -> dict`
- Produces: `append_slot_event(data_dir: Path, run_id: str, stage: str, level: str, message: str, metadata: dict | None = None) -> int`
- Produces: `events_after(data_dir: Path, run_id: str, after_id: int, limit: int = 100) -> list[dict]`

- [ ] **Step 1: Write state, cutoff, and concurrency tests**

```python
def kst(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=KST)

def checked_result() -> dict:
    return {
        "status": "reservable",
        "normalized_topic": "검증된 수동 소재",
        "topic_payload": {"format": "story", "topic": "검증된 수동 소재"},
        "visual": {"level": "high", "reservable": True},
    }

def seed_reserved(data_dir: Path, run_id: str) -> None:
    create_check(data_dir, run_id, {"topic_input": "검증된 수동 소재"}, kst(8, 40))
    save_check_result(data_dir, run_id, checked_result(), kst(8, 41))
    reserve_checked_topic(data_dir, run_id, kst(8, 42))

def test_slot_one_window_uses_kst_cutoff_and_upload_time():
    window = slot_window("20260810-1")
    assert window.production_at.isoformat() == "2026-08-10T09:00:00+09:00"
    assert window.upload_at.isoformat() == "2026-08-10T11:00:00+09:00"

def test_reservation_is_rejected_at_production_cutoff(tmp_path):
    create_check(tmp_path, "20260810-1", {"topic_input": "단종"}, kst(8, 50))
    save_check_result(tmp_path, "20260810-1", checked_result(), kst(8, 51))
    with pytest.raises(SlotConflict, match="입력 시간이 종료"):
        reserve_checked_topic(tmp_path, "20260810-1", kst(9, 0))

def test_only_one_worker_can_lock_reserved_slot(tmp_path):
    seed_reserved(tmp_path, "20260810-1")
    assert lock_reserved_slot(tmp_path, "20260810-1", "worker-a", kst(9, 0))
    assert lock_reserved_slot(tmp_path, "20260810-1", "worker-b", kst(9, 0)) is None
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_slot_reservations.py`

Expected: FAIL because `app.services.slot_reservations` does not exist.

- [ ] **Step 3: Implement SQLite tables, KST windows, and guarded transitions**

Create `slot_reservations` with a primary key `run_id` and columns for mode, original input, normalized topic, optional constraints, serialized check result, state, stage, attempt, worker, timestamps, approval/rejection, artifact path, and video ID. Create `slot_events` with an autoincrement ID, run ID, stage, level, sanitized message, metadata JSON, and timestamp. Use `BEGIN IMMEDIATE` in lock and transition operations so state checks and updates are atomic.

```python
SLOT_TIMES = {
    1: (time(9, 0), time(11, 0)),
    2: (time(12, 0), time(14, 0)),
    3: (time(15, 0), time(17, 0)),
    4: (time(19, 0), time(21, 0)),
}

ACTIVE_STATES = {
    "locked", "researching", "writing", "producing", "quality_check", "uploading"
}

ALLOWED_TRANSITIONS = {
    "draft": {"checking", "cancelled"},
    "checking": {"reservable", "needs_input", "failed"},
    "reservable": {"reserved", "checking", "cancelled"},
    "reserved": {"locked", "checking", "cancelled"},
    "locked": {"researching", "failed"},
    "researching": {"writing", "failed"},
    "writing": {"producing", "failed"},
    "producing": {"quality_check", "failed"},
    "quality_check": {"review_ready", "failed"},
    "review_ready": {"approved", "rejected", "held"},
    "held": {"approved", "rejected"},
    "approved": {"uploading"},
    "uploading": {"uploaded", "failed"},
    "failed": {"checking", "skipped"},
    "rejected": {"checking", "skipped"},
}
```

Limit event messages to 500 characters, metadata JSON to 4 KiB, and allow only `debug`, `info`, `warning`, and `error` levels. Replace values matching token/key/credential patterns with `[redacted]` before insertion.

- [ ] **Step 4: Run persistence tests**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_slot_reservations.py`

Expected: PASS.

- [ ] **Step 5: Commit the persistence layer**

```powershell
git add app/services/slot_reservations.py tests/test_slot_reservations.py
git commit -m "기능: 회차별 소재 예약 상태 저장"
```

---

### Task 2: Requested-Topic Interpretation and Visual Preflight

**Files:**
- Create: `app/services/manual_topic.py`
- Modify: `app/agents/researcher.py`
- Create: `tests/test_manual_topic.py`
- Modify: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: reservation event and transition functions from Task 1.
- Produces: `check_requested_topic(data_dir: Path, run_id: str, request: ManualTopicInput, *, call_agent_fn=call_agent) -> dict`
- Produces: `build_requested_topic_prompt(request: ManualTopicInput, recent_topics: list[str]) -> str`
- Produces: `assess_visual_feasibility(topic: dict) -> dict`
- Produces: `ManualTopicInput(topic_input: str, emphasis: str = "", include_text: str = "", exclude_text: str = "", reference_urls: tuple[str, ...] = ())`

- [ ] **Step 1: Write ambiguity, off-channel, verification, and media tests**

```python
def agent_returning(payload: dict):
    return lambda **_: json.dumps(payload, ensure_ascii=False)

def test_single_ambiguous_word_requires_user_choice(tmp_path):
    response = {
        "needs_clarification": True,
        "interpretations": ["조선 제6대 왕 단종", "단종된 제품 이야기"],
    }
    result = check_requested_topic(
        tmp_path,
        "20260810-1",
        ManualTopicInput(topic_input="단종"),
        call_agent_fn=agent_returning(response),
    )
    assert result["status"] == "needs_input"
    assert len(result["interpretations"]) == 2

def test_off_channel_topic_is_allowed_with_warning(tmp_path):
    response = {"needs_clarification": False, "channel_fit": False, "topic": valid_story_topic(category="economy")}
    result = check_requested_topic(
        tmp_path,
        "20260810-2",
        ManualTopicInput(topic_input="단종된 게임기"),
        call_agent_fn=agent_returning(response),
    )
    assert result["status"] == "reservable"
    assert result["channel_warning"] is True

def test_visual_preflight_blocks_unrelated_only_media(monkeypatch):
    monkeypatch.setattr(manual_topic, "search_visual_candidates", lambda _: [])
    result = assess_visual_feasibility(verified_topic(ai_opening_allowed=False))
    assert result["level"] == "insufficient"
    assert result["reservable"] is False
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_topic.py tests/test_story_prompts.py`

Expected: FAIL because the requested-topic service and prompt are absent.

- [ ] **Step 3: Implement a grounded requested-topic prompt**

The prompt must interpret the user's intent, return 2–3 choices when ambiguous, otherwise emit a valid `StoryTopicContract` using only grounded facts. It must include the optional emphasis/include/exclude fields and recent 14-day topic exclusions. It may set `channel_fit=false` but must not reject solely for channel mismatch.

```python
@dataclass(frozen=True)
class ManualTopicInput:
    topic_input: str
    emphasis: str = ""
    include_text: str = ""
    exclude_text: str = ""
    reference_urls: tuple[str, ...] = ()

def check_requested_topic(
    data_dir: Path,
    run_id: str,
    request: ManualTopicInput,
    *,
    call_agent_fn=call_agent,
) -> dict:
    append_slot_event(data_dir, run_id, "topic_check", "info", "입력 소재를 해석하고 있습니다")
    raw = extract_json(call_agent_fn(
        prompt=build_requested_topic_prompt(request, _load_recent_topics(data_dir)),
        agent_name="manual-topic-researcher",
        prefer="gemini",
        use_search=True,
    ))
    if raw.get("needs_clarification"):
        return {"status": "needs_input", "interpretations": raw["interpretations"][:3]}
    topic = validate_topic({**raw["topic"], "verification_method": "grounded_search"}, "story")
    visual = assess_visual_feasibility(topic)
    return build_check_result(topic, visual, raw.get("channel_fit", True))
```

Visual preflight must inspect exact Wikimedia-compatible queries, configured free-stock providers, matching items in the AI opening library, and whether credit mode permits one new AI opening. Return counts and booleans, not downloaded media. Mark `insufficient` only when exact, related-stock, reusable-AI, and permitted-new-AI paths are all unavailable.

- [ ] **Step 4: Run the focused tests**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_topic.py tests/test_story_prompts.py`

Expected: PASS.

- [ ] **Step 5: Commit requested-topic checking**

```powershell
git add app/services/manual_topic.py app/agents/researcher.py tests/test_manual_topic.py tests/test_story_prompts.py
git commit -m "기능: 입력 소재 검증과 시각자료 사전검사"
```

---

### Task 3: Manual Prebuild Worker and Persistent Events

**Files:**
- Create: `app/services/manual_slot_pipeline.py`
- Modify: `scripts/prepare_next_slot.py`
- Modify: `app/services/slot_prebuild.py`
- Create: `tests/test_manual_slot_pipeline.py`
- Modify: `tests/test_slot_prebuild.py`

**Interfaces:**
- Consumes: `lock_reserved_slot`, `transition_slot`, `append_slot_event` from Task 1.
- Consumes: checked `topic_payload` from Task 2.
- Produces: `manual_reservation_for_prebuild(data_dir: Path, run_id: str) -> dict | None`
- Produces: `run_manual_prebuild(data_dir: Path, ffmpeg_path: str, run_id: str, *, writer_fn=run_writer, producer_fn=run_producer) -> dict`
- Changes: `prepare_slot(data_dir: Path, ffmpeg_path: str, slot: int, *, now_fn=None, use_lock=True, lock_wait_seconds=5400, lock_poll_seconds=30) -> dict` resolves manual reservation before calling the unchanged automatic researcher path.

- [ ] **Step 1: Write manual/automatic routing and event tests**

```python
def test_no_reservation_uses_existing_automatic_researcher(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(prepare_next_slot, "run_researcher", lambda *a, **k: calls.append("auto") or valid_topic())
    prepare_next_slot.prepare_slot(tmp_path, "ffmpeg", 1, now_fn=lambda: kst(8, 55), use_lock=False)
    assert calls == ["auto"]

def test_reserved_slot_uses_checked_topic_without_auto_researcher(tmp_path, monkeypatch):
    reserve_manual_slot(tmp_path, "20260810-1", checked_topic())
    monkeypatch.setattr(prepare_next_slot, "run_researcher", lambda *a, **k: pytest.fail("automatic researcher called"))
    result = run_manual_prebuild(tmp_path, "ffmpeg", "20260810-1", writer_fn=fake_writer, producer_fn=fake_producer)
    assert result["state"] == "review_ready"
    assert read_reservation(tmp_path, "20260810-1")["artifact_path"].endswith("20260810-1")

def test_manual_failure_records_stage_and_releases_worker(tmp_path):
    reserve_manual_slot(tmp_path, "20260810-1", checked_topic())
    with pytest.raises(RuntimeError, match="render failed"):
        run_manual_prebuild(tmp_path, "ffmpeg", "20260810-1", producer_fn=raising_producer)
    state = read_reservation(tmp_path, "20260810-1")
    assert state["state"] == "failed"
    assert state["worker_id"] is None
```

- [ ] **Step 2: Run tests and verify manual routing is absent**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py`

Expected: FAIL because manual prebuild routing does not exist.

- [ ] **Step 3: Implement the manual worker**

`run_manual_prebuild` must acquire the existing `data/recovery/pipeline.lock`, create `manual-prebuild-<run_id>-<attempt>` under staging, write the previously checked `topic.json`, and invoke the current writer, producer, quality gate, and promotion functions. Before each boundary it transitions and appends a user-safe event. After promotion it writes `manual_review.json` beside `prepared.json` containing `run_id`, `attempt`, `state=review_ready`, and the reservation topic hash.

```python
STAGES = (
    ("researching", "검증된 소재를 준비했습니다"),
    ("writing", "대본을 작성하고 있습니다"),
    ("producing", "음성·영상·자막을 합성하고 있습니다"),
    ("quality_check", "최종 영상 품질을 검사하고 있습니다"),
)
```

In `prepare_slot`, calculate the target run ID first, then:

```python
manual = manual_reservation_for_prebuild(data_dir, initial_run_id)
if manual is not None:
    return run_manual_prebuild(data_dir, ffmpeg_path, initial_run_id)
# existing automatic implementation continues unchanged below
```

Keep the automatic prebuild test's call sequence and result shape unchanged.

- [ ] **Step 4: Run worker and regression tests**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py tests/test_recovery.py tests/test_quality_gate.py`

Expected: PASS.

- [ ] **Step 5: Commit the manual worker**

```powershell
git add app/services/manual_slot_pipeline.py scripts/prepare_next_slot.py app/services/slot_prebuild.py tests/test_manual_slot_pipeline.py tests/test_slot_prebuild.py
git commit -m "기능: 예약 소재 회차 영상 사전제작"
```

---

### Task 4: Approval Gate, Rejection, Retry, and Scheduled Upload

**Files:**
- Create: `app/services/manual_slot_actions.py`
- Modify: `app/agents/orchestrator.py`
- Modify: `scripts/run_scheduled.py`
- Modify: `app/services/temp_cleanup.py`
- Create: `tests/test_manual_slot_actions.py`
- Modify: `tests/test_uploader.py`
- Modify: `tests/test_recovery.py`

**Interfaces:**
- Produces: `upload_decision(data_dir: Path, run_id: str, now: datetime) -> Literal["automatic", "approved", "hold"]`
- Produces: `approve_slot(data_dir: Path, run_id: str, now: datetime) -> dict`
- Produces: `reject_slot(data_dir: Path, run_id: str, reason: str, now: datetime) -> dict`
- Produces: `retry_slot(data_dir: Path, run_id: str, mode: Literal["same_topic", "new_topic"], now: datetime) -> dict`
- Produces: `skip_slot(data_dir: Path, run_id: str, now: datetime) -> dict`
- Produces: `cleanup_rejected_artifacts(data_dir: Path, retention_days: int, now: datetime) -> dict`

- [ ] **Step 1: Write approval and upload-gate tests**

```python
def test_unapproved_manual_slot_is_held_without_auto_fallback(tmp_path, monkeypatch):
    seed_review_ready_slot(tmp_path, "20260810-1")
    calls = []
    monkeypatch.setattr(orchestrator, "run_researcher", lambda *a, **k: calls.append("researcher"))
    result = asyncio.run(run_pipeline(tmp_path, "ffmpeg", slot=1))
    assert result["stages"]["uploader"] == {"status": "skipped", "reason": "manual_review_required"}
    assert calls == []

def test_approval_before_upload_waits_for_scheduler(tmp_path):
    seed_review_ready_slot(tmp_path, "20260810-1")
    result = approve_slot(tmp_path, "20260810-1", kst(10, 30))
    assert result["upload_action"] == "scheduled"

def test_approval_after_upload_requests_immediate_single_upload(tmp_path):
    seed_held_slot(tmp_path, "20260810-1")
    result = approve_slot(tmp_path, "20260810-1", kst(11, 5))
    assert result["upload_action"] == "immediate"

def test_reject_archives_artifact_and_allows_new_check(tmp_path):
    seed_review_ready_slot(tmp_path, "20260810-1", with_artifact=True)
    result = reject_slot(tmp_path, "20260810-1", "대본 수정 필요", kst(10, 20))
    assert Path(result["archived_path"]).is_dir()
    assert result["state"] == "rejected"
```

- [ ] **Step 2: Run tests and verify approval gating fails**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_actions.py tests/test_uploader.py tests/test_recovery.py`

Expected: FAIL because manual approval decisions do not exist.

- [ ] **Step 3: Implement upload decisions and artifact lifecycle**

At the beginning of `run_pipeline`, call `upload_decision` after deriving `run_id` and before creating or regenerating files. Return a successful held run log for `hold`; do not invoke researcher, writer, producer, or uploader. `approved` continues through the existing prepared-package reuse and uploader. `automatic` executes the original path unchanged.

`reject_slot` must verify there is no live worker or global lock owner for the run, move `data/work/<run_id>` to `data/rejected/<run_id>-attempt-<N>`, clear the active artifact path, and transition to `rejected`. `retry_slot(new_topic)` transitions to `checking` and clears normalized/check fields; `retry_slot(same_topic)` transitions to `reserved` only when the last check result is still valid.

When approval returns `immediate`, the API layer in Task 5 schedules exactly one `run_pipeline(data_dir, ffmpeg_path, slot=slot_number)` background call. Atomic transition from `approved` to `uploading` prevents duplicate clicks and scheduled cron overlap.

- [ ] **Step 4: Run scheduler, upload, recovery, and cleanup tests**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_manual_slot_actions.py tests/test_uploader.py tests/test_recovery.py tests/test_temp_cleanup.py`

Expected: PASS.

- [ ] **Step 5: Commit approval and lifecycle behavior**

```powershell
git add app/services/manual_slot_actions.py app/agents/orchestrator.py scripts/run_scheduled.py app/services/temp_cleanup.py tests/test_manual_slot_actions.py tests/test_uploader.py tests/test_recovery.py
git commit -m "기능: 수동 회차 승인과 반려 업로드 제어"
```

---

### Task 5: FastAPI Slot Management Endpoints

**Files:**
- Create: `app/routes/__init__.py`
- Create: `app/routes/slots.py`
- Modify: `app/main.py`
- Create: `tests/test_slot_api.py`
- Modify: `tests/test_monitor_api.py`

**Interfaces:**
- Consumes: Tasks 1–4 services.
- Produces: all `/api/slots` routes defined in the design.
- Produces: `require_dashboard_token(x_token: str = Header(default="")) -> None`

- [ ] **Step 1: Write endpoint authentication and state tests**

```python
def checked_request() -> dict:
    return {"checked": True}

def test_put_reservation_requires_dashboard_token(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")
    response = client.put("/api/slots/20260810-1/reservation", json=checked_request())
    assert response.status_code == 401

def test_slot_list_exposes_auto_and_manual_cards(client, seeded_slots):
    payload = client.get("/api/slots?date=2026-08-10").json()
    assert [card["slot"] for card in payload["slots"]] == [1, 2, 3, 4]
    assert payload["slots"][0]["mode"] == "manual"
    assert payload["slots"][1]["mode"] == "auto"

def test_video_download_requires_review_artifact_and_token(client, review_ready_slot):
    response = client.get("/api/slots/20260810-1/video", headers={"X-Token": "secret"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
```

- [ ] **Step 2: Run API tests and verify route failures**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py tests/test_monitor_api.py`

Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement the router and background jobs**

Define Pydantic request models with `topic_input` length 1–300, optional text fields up to 500, at most five HTTPS reference URLs, rejection reason up to 300, and retry mode enum. Validate `run_id` against `\d{8}-[1-4]` before service calls.

`POST check-topic` sets the state to `checking`, starts one FastAPI background task, and returns HTTP 202. `GET /api/slots/{run_id}` and events polling expose the eventual result. Reservation, approve, reject, retry, skip, and video routes use the shared token dependency. Map state conflicts to 409, invalid cutoff/input to 422, and missing slots/artifacts to 404.

```python
router = APIRouter(prefix="/api/slots", tags=["slots"])

@router.get("")
def slots(day: date = Query(alias="date")):
    return {"date": day.isoformat(), "slots": list_slot_cards(DATA_DIR, day, datetime.now(KST))}

@router.post("/{run_id}/check-topic", status_code=202, dependencies=[Depends(require_dashboard_token)])
def check_topic(run_id: str, body: TopicCheckRequest, tasks: BackgroundTasks):
    create_check(DATA_DIR, run_id, body.model_dump(), datetime.now(KST))
    tasks.add_task(run_topic_check_job, DATA_DIR, run_id, body.to_domain())
    return {"accepted": True, "run_id": run_id, "state": "checking"}
```

Serve MP4 with `FileResponse` only after resolving the artifact path from the database and confirming the resolved path stays under `DATA_DIR/work`.

- [ ] **Step 4: Run API and existing monitor tests**

Run: `venv\Scripts\python.exe -m pytest -q tests/test_slot_api.py tests/test_monitor_api.py`

Expected: PASS.

- [ ] **Step 5: Commit the API layer**

```powershell
git add app/routes app/main.py tests/test_slot_api.py tests/test_monitor_api.py
git commit -m "기능: 회차 소재 예약과 검수 API"
```

---

### Task 6: Frontend Slot API Client and View-State Helpers

**Files:**
- Create: `D:\ms\shorts-factory-fe\src\slotApi.js`
- Create: `D:\ms\shorts-factory-fe\src\slotState.js`
- Create: `D:\ms\shorts-factory-fe\src\slotState.test.js`

**Interfaces:**
- Produces: `createSlotClient({ getToken, fetchImpl = fetch })`
- Produces: `slotActions(slot, now) -> { canCheck, canEdit, canApprove, canReject, canRetry, canSkip }`
- Produces: `slotProgress(state) -> { index, total, percent, label }`
- Produces: `formatCountdown(productionAt, now) -> string`

- [ ] **Step 1: Write frontend helper tests**

```javascript
function slot(overrides = {}) {
  return {
    run_id: '20260810-1',
    state: 'auto',
    worker_id: null,
    production_at: '2026-08-10T09:00:00+09:00',
    upload_at: '2026-08-10T11:00:00+09:00',
    ...overrides,
  }
}

test('reserved slot disables new input at production cutoff', () => {
  const actions = slotActions(slot({ state: 'reserved', production_at: '2026-08-10T09:00:00+09:00' }), new Date('2026-08-10T09:00:00+09:00'))
  assert.equal(actions.canEdit, false)
})

test('rejected slot permits a replacement topic after worker release', () => {
  const actions = slotActions(slot({ state: 'rejected', worker_id: null }), new Date('2026-08-10T10:00:00+09:00'))
  assert.equal(actions.canCheck, true)
})

test('review ready exposes approval and rejection only', () => {
  const actions = slotActions(slot({ state: 'review_ready' }), new Date())
  assert.equal(actions.canApprove, true)
  assert.equal(actions.canReject, true)
  assert.equal(actions.canEdit, false)
  assert.equal(actions.canRetry, false)
})
```

- [ ] **Step 2: Run Node tests and verify missing modules**

Run: `npm test -- --test-name-pattern="slot"`

Working directory: `D:\ms\shorts-factory-fe`

Expected: FAIL because `slotState.js` is absent.

- [ ] **Step 3: Implement the token-aware client and pure state helpers**

The client adds `X-Token` only to protected calls, parses FastAPI `detail`, and exposes `listSlots`, `getSlot`, `checkTopic`, `reserve`, `cancel`, `events`, `fetchVideoBlob`, `approve`, `reject`, `retry`, and `skip`. Store no token inside the module; the component supplies a `sessionStorage` getter.

```javascript
const ACTIVE_STATES = new Set(['checking', 'locked', 'researching', 'writing', 'producing', 'quality_check', 'uploading'])

export function shouldPollFast(slot) {
  return ACTIVE_STATES.has(slot.state) || slot.state === 'review_ready' || slot.state === 'held'
}
```

- [ ] **Step 4: Run frontend helper tests**

Run: `npm test`

Working directory: `D:\ms\shorts-factory-fe`

Expected: PASS.

- [ ] **Step 5: Commit frontend data helpers**

```powershell
git -C D:\ms\shorts-factory-fe add src/slotApi.js src/slotState.js src/slotState.test.js
git -C D:\ms\shorts-factory-fe commit -m "기능: 회차 예약 API와 상태 도우미"
```

---

### Task 7: Frontend Slot Cards, Topic Form, Logs, and Video Review

**Files:**
- Create: `D:\ms\shorts-factory-fe\src\SlotManager.jsx`
- Create: `D:\ms\shorts-factory-fe\src\SlotCard.jsx`
- Create: `D:\ms\shorts-factory-fe\src\TopicCheckForm.jsx`
- Create: `D:\ms\shorts-factory-fe\src\SlotReview.jsx`
- Create: `D:\ms\shorts-factory-fe\src\SlotEvents.jsx`
- Modify: `D:\ms\shorts-factory-fe\src\App.jsx`
- Modify: `D:\ms\shorts-factory-fe\src\index.css`

**Interfaces:**
- Consumes: `createSlotClient`, `slotActions`, `slotProgress`, and `formatCountdown` from Task 6.
- Produces: `<SlotManager />` mounted above the existing pipeline summary.

- [ ] **Step 1: Add the manager with explicit polling lifecycle**

`SlotManager` loads today's and tomorrow's cards, keeps the dashboard token in `sessionStorage`, polls every two seconds while any slot is active, and every thirty seconds otherwise. Abort obsolete fetches on date changes and component unmount. Keep existing video/history pagination polling independent.

```jsx
<section className="card slot-manager">
  <div className="slot-manager-heading">
    <h2>회차별 제작 관리</h2>
    <input type="password" value={token} onChange={saveSessionToken} aria-label="관리 토큰" />
  </div>
  <div className="slot-grid">
    {slots.map(slot => <SlotCard key={slot.run_id} slot={slot} client={client} onChanged={reload} />)}
  </div>
</section>
```

- [ ] **Step 2: Implement topic confirmation and reservation UI**

The form includes slot selection through its parent card, topic input, optional emphasis/include/exclude, up to five reference URL fields, and `소재 확인`. Render interpretation choices for `needs_input`; render normalized title, question, channel warning, verification method, media counts, AI availability, and feasibility level for `reservable`. Only `이 소재 예약` changes the slot to manual mode.

- [ ] **Step 3: Implement progress, incremental logs, and failure actions**

`SlotCard` displays automatic/manual badge, KST production/upload times, cutoff countdown, normalized topic, progress bar, and last three events. `SlotEvents` requests `after_id=<last ID>` and appends deduplicated events. Failed and rejected cards display user-safe reason plus buttons allowed by `slotActions`.

- [ ] **Step 4: Implement authenticated MP4 preview and review actions**

`SlotReview` calls `fetchVideoBlob`, creates an object URL, renders `<video controls playsInline>`, and revokes the URL on replacement or unmount. Show title, script scenes, description, tags, QC report, verification method, and AI/stock source counts. `승인`, `반려`, and retry actions require a confirmation dialog and disable while their request is pending.

- [ ] **Step 5: Add responsive styling and update stale schedule copy**

Use the existing dark palette. Four cards render in two columns above 760 px and one column below. Add visible focus states, state badge colors, a horizontally safe log pane, and a 9:16 preview capped at 360 px width. Change the header and empty-state copy from three upload times to `11:00·14:00·17:00·21:00`.

- [ ] **Step 6: Run frontend tests and production build**

Run: `npm test && npm run build`

Working directory: `D:\ms\shorts-factory-fe`

Expected: all Node tests pass and Vite creates `dist` without JSX/build errors.

- [ ] **Step 7: Commit the visible dashboard feature**

```powershell
git -C D:\ms\shorts-factory-fe add src/SlotManager.jsx src/SlotCard.jsx src/TopicCheckForm.jsx src/SlotReview.jsx src/SlotEvents.jsx src/App.jsx src/index.css
git -C D:\ms\shorts-factory-fe commit -m "기능: 회차별 소재 예약과 영상 검수 화면"
```

---

### Task 8: Full Regression, Deployment, and Operational Proof

**Files:**
- Modify: `README.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `D:\ms\shorts-factory-fe\README.md`

**Interfaces:**
- Verifies all previous interfaces together; adds no new production interface.

- [ ] **Step 1: Add operator documentation**

Document the four production/upload cutoffs, how to enter `DASHBOARD_TOKEN` in the session-only frontend field, manual reservation workflow, approval-after-deadline immediate upload, rejection archive retention, and how to return a pre-production reservation to automatic mode.

- [ ] **Step 2: Run the complete backend suite**

Run: `venv\Scripts\python.exe -m pytest -q`

Working directory: `D:\ms\shorts-factory-be`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run the complete frontend suite and build**

Run: `npm test && npm run build`

Working directory: `D:\ms\shorts-factory-fe`

Expected: all tests pass and the production bundle builds.

- [ ] **Step 4: Run local API integration checks**

Start FastAPI against a temporary `DATA_DIR`, then verify:

```text
GET slots → four auto cards
POST check-topic without token → 401
POST check-topic with token → 202/checking
poll detail/events → reservable or needs_input
reserve → reserved
invoke prebuild → review_ready without YouTube upload
download video → MP4 bytes
reject → archived and replacement input enabled
```

- [ ] **Step 5: Deploy backend with recoverable backup**

Back up only the changed server source and cron-facing scripts under `/home/ubuntu/backups/manual-slot-<timestamp>`, transfer the tested files, restart the FastAPI service, and verify `/api/health`. Do not alter `.env` except confirming `DASHBOARD_TOKEN` is already set.

- [ ] **Step 6: Deploy frontend and verify the live dashboard**

Push the frontend `main` branch so its existing deployment flow builds the dashboard. Open the deployed page, enter the token, confirm four cards are visible, and confirm existing videos/history pagination still loads.

- [ ] **Step 7: Prove automatic-path preservation and manual hold behavior**

On the server, run a dry integration with one temporary automatic slot fixture and one manual slot fixture. Confirm the automatic fixture calls the existing researcher/writer/producer/uploader sequence, while the manual fixture stops at `review_ready` and records no uploaded video row before approval.

- [ ] **Step 8: Commit documentation and push both repositories**

```powershell
git add README.md docs/OPERATIONS.md
git commit -m "문서: 회차별 수동 소재 운영 절차"
git push origin main

git -C D:\ms\shorts-factory-fe add README.md
git -C D:\ms\shorts-factory-fe commit -m "문서: 회차별 소재 예약 화면 사용법"
git -C D:\ms\shorts-factory-fe push origin main
```
