# Pipeline Auto Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent semantically truncated title narration, recover verified scripts without manual intervention, retry safe failed slots once after 15 minutes, expose recovery state in monitoring, and regenerate/upload slot `20260721-1` once.

**Architecture:** Keep content production inside the existing orchestrator. Add deterministic script construction to the writer and put scheduling/recovery state in a focused `app/services/recovery.py` plus a thin `scripts/run_scheduled.py` wrapper. Merge recovery JSON into the existing paginated history API and render it in the current React history list.

**Tech Stack:** Python 3, Pydantic, FastAPI, pytest, filesystem JSON state, Linux `flock`, React 18, Vite, Node test runner, cron, ffmpeg, YouTube Data API.

## Global Constraints

- Do not modify or commit `.env` or `credentials/`.
- Keep scheduled slots at 11:00, 17:00, and 21:00 KST.
- Keep `DAILY_UPLOAD_LIMIT=3` and the code maximum of six uploads.
- Retry only researcher/writer/producer failures when SQLite and the run log show no successful or ambiguous upload.
- Preserve verified facts; deterministic fallback must not invent facts.
- Back up server code, crontab, SQLite, logs, and the target work directory before mutation.

---

### Task 1: Complete title narration

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `_spoken_intro(title: str) -> str`
- Produces: a complete normalized title with no word-count truncation

- [ ] Add a regression test asserting `_spoken_intro("딸기우유 빛깔 호수가 분홍빛을 유지하는 신비로운 이유")` returns the complete title.
- [ ] Run `pytest -q tests/test_story_producer.py -k spoken_intro` and confirm the current 22-character behavior fails.
- [ ] Remove suffix and word-count truncation; retain only surrounding whitespace and terminal punctuation normalization.
- [ ] Run the focused test and commit `fix: narrate complete story titles`.

### Task 2: Deterministic verified script fallback

**Files:**
- Modify: `app/agents/writer.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Produces: `build_verified_story_script(topic: dict) -> dict`
- Adds returned metadata key: `writer_mode` with `llm`, `llm_retry`, or `verified_template`

- [ ] Add a test where two `call_agent` responses are incomplete and assert a valid 8-scene story is saved with `writer_mode == "verified_template"`.
- [ ] Assert every fallback narration value is composed only from `topic`, `hook_angle`, `core_question`, and the exact `facts[].claim/value`; assert visuals come only from `visual_plan[].keywords`.
- [ ] Run the focused test and confirm failure.
- [ ] Implement an 8-role template using exact verified inputs, repeat facts rather than inventing missing detail, allocate scene durations totaling 56 seconds, and validate it with `validate_script`.
- [ ] Mark successful first/second LLM results as `llm`/`llm_retry`; keep unknown extra metadata outside Pydantic validation and add it after validation.
- [ ] Run writer tests and commit `feat: add verified script fallback`.

### Task 3: Safe recovery state and scheduled wrapper

**Files:**
- Create: `app/services/recovery.py`
- Create: `scripts/run_scheduled.py`
- Create: `tests/test_recovery.py`
- Modify: `scripts/run_daily.py`

**Interfaces:**
- Produces: `RecoveryState` JSON fields `run_id`, `attempts`, `status`, `failed_stage`, `last_error`, `next_retry_at`, `updated_at`
- Produces: `is_safe_to_retry(run_log: dict, uploaded_dates: set[str]) -> bool`
- Produces: `run_with_recovery(data_dir: Path, ffmpeg_path: str, slot: int, delay_seconds: int = 900) -> dict`

- [ ] Test that writer/producer failure without an upload is retryable, uploader success/ambiguous uploader failure is not, and next retry equals failure time plus 15 minutes.
- [ ] Test the wrapper calls the pipeline at most twice and writes `scheduled`, `running`, `recovered`, or `exhausted` atomically.
- [ ] Run `pytest -q tests/test_recovery.py` and confirm missing-module failures.
- [ ] Implement atomic JSON replacement with a temporary sibling file, SQLite uploaded-date lookup, and one delayed retry.
- [ ] Use a lock file opened with exclusive creation so concurrent identical slots return `already_running`; remove only the exact acquired lock in `finally`.
- [ ] Keep `run_daily.py` unchanged for manual execution and make cron use `run_scheduled.py`.
- [ ] Run recovery tests and commit `feat: retry safe scheduled runs once`.

### Task 4: Recovery-aware history API

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_monitor_api.py`

**Interfaces:**
- `/api/history` adds optional `recovery` per run without changing pagination metadata.

- [ ] Add history fixtures under `recovery/` and assert the matching run receives the recovery object while pagination order remains stable.
- [ ] Run the focused monitor test and confirm failure.
- [ ] Load `data/recovery/{run.date}.json` defensively and merge only valid dictionaries.
- [ ] Run monitor tests and commit `feat: expose pipeline recovery status`.

### Task 5: Recovery status in frontend monitoring

**Files:**
- Create: `D:/ms/shorts-factory-fe/src/recovery.js`
- Create: `D:/ms/shorts-factory-fe/src/recovery.test.js`
- Modify: `D:/ms/shorts-factory-fe/src/App.jsx`
- Modify: `D:/ms/shorts-factory-fe/src/index.css`

**Interfaces:**
- Produces: `formatRecovery(recovery) -> { label, className, detail } | null`

- [ ] Add Node tests for `scheduled`, `running`, `recovered`, and `exhausted` labels, attempts, and next retry time.
- [ ] Run `npm test` and confirm the missing module failure.
- [ ] Implement the formatter and render a badge/detail under each history row without truncating the core failure reason.
- [ ] Add responsive badge/detail styles and keep pagination unchanged.
- [ ] Run `npm test && npm run build` and commit frontend changes `feat: show pipeline recovery status`.

### Task 6: Full verification and deployment

**Files:**
- Backend source/tests from Tasks 1-4
- Frontend source/tests from Task 5
- Remote crontab and deployment files

- [ ] Run backend `pytest -q`, `git diff --check`, frontend `npm test`, and `npm run build`.
- [ ] Commit remaining backend changes and push both repositories' current `main` branches.
- [ ] Back up remote `app/`, `scripts/`, `tests/`, `.env`, `credentials/`, `data/videos.sqlite`, `data/logs`, `data/work/20260721-1`, and `crontab -l`.
- [ ] Copy backend files, run server tests, and replace only three pipeline cron lines with `scripts/run_scheduled.py 1|2|3` at 11/17/21.
- [ ] Restart `shorts-dashboard`; confirm cron, dashboard, and `/api/health` are active.
- [ ] Deploy frontend using its existing Vercel workflow and verify history renders against the live API.

### Task 7: Rebuild and re-upload `20260721-1`

**Files:**
- Remote `data/work/20260721-1/`
- Remote `data/logs/run-20260721-1.json`
- Remote `data/videos.sqlite`

- [ ] Confirm the old YouTube ID is absent or deleted and no pipeline process is running.
- [ ] Back up the exact DB, log, and work directory again immediately before reset.
- [ ] Delete only the SQLite row where `date='20260721-1'`, plus that directory's `output.mp4` and `produce_log.json`; preserve `topic.json` and `script.json`.
- [ ] Run the same slot through `scripts/run_scheduled.py 1` with retry delay overridden to zero for the supervised recovery run.
- [ ] Verify the generated intro text equals the full title, video is 1080x1920 H.264/AAC with audio and duration 60-75 seconds, fallback shots are recorded, and the run log reports success.
- [ ] Confirm exactly one new public video ID in SQLite and return its YouTube Shorts URL.
