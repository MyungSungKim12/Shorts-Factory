# Credit-Aware Premium Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Vertex Gemini, Gemini TTS, and two-candidate Veo generation while automatically returning to no-new-paid-call operation at an estimated 80,000 KRW balance.

**Architecture:** A focused credit guard owns mode decisions and an atomic local spend ledger. Existing provider adapters consult it before paid calls and preserve their current free fallbacks. AI media remains in the permanent asset library and can be reused in free mode.

**Tech Stack:** Python 3.12, pytest, Google ADC, Vertex AI REST/Google Gen AI SDK, Cloud TTS REST, SQLite, ffmpeg.

## Global Constraints

- Never commit `.env` or credentials.
- Never exceed six YouTube uploads per day.
- The automatic paid-call cutoff is an estimated remaining balance of 80,000 KRW.
- Free mode performs no new Vertex Gemini, Gemini TTS, or Veo request.
- Ready AI assets remain reusable and are never removed by seven-day work cleanup.
- Replace the 17:00 package only after the new package passes the full quality gate.

---

### Task 1: Credit guard and atomic spend ledger

**Files:**
- Create: `app/services/credit_guard.py`
- Create: `tests/test_credit_guard.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `paid_features_enabled(data_dir: Path) -> bool`
- Produces: `reserve_cost(data_dir: Path, feature: str, estimated_usd: float, run_id: str) -> CostReservation`
- Produces: `commit_cost(reservation: CostReservation, actual_usd: float | None = None) -> None`
- Produces: `cancel_cost(reservation: CostReservation) -> None`

- [ ] Write tests for 80,000 KRW cutoff, reservations, failed-call cancellation, corrupt-state fail-safe, and manual free override.
- [ ] Run `python -m pytest tests/test_credit_guard.py -q` and verify the missing module failure.
- [ ] Implement atomic JSON state and append-only JSONL event recording.
- [ ] Run the credit guard tests and commit.

### Task 2: Vertex Gemini premium provider with current free fallback

**Files:**
- Modify: `app/services/claude_client.py`
- Create: `tests/test_vertex_llm.py`
- Modify: `tests/test_claude_client.py`

**Interfaces:**
- Consumes: `paid_features_enabled`, cost reservation lifecycle.
- Produces: `_vertex_gemini_generate(prompt, max_tokens, grounded) -> str`.

- [ ] Write failing tests proving premium-first routing, grounded configuration, cost logging, and free-mode legacy routing.
- [ ] Run the targeted tests and verify expected failures.
- [ ] Implement ADC Vertex Gemini 2.5 Flash calls without removing current Gemini/Groq providers.
- [ ] Run provider tests and commit.

### Task 3: Gemini controllable female narration

**Files:**
- Modify: `app/services/tts.py`
- Modify: `tests/test_tts.py`

**Interfaces:**
- Consumes: credit guard.
- Produces: premium Gemini TTS using `gemini-2.5-flash-tts`, `ko-KR`, `Kore`.
- Preserves: Gemini TTS failure -> Chirp 3 HD Kore -> gTTS.

- [ ] Write failing tests for style prompt payload, free-mode Chirp selection, and both fallback levels.
- [ ] Run targeted tests and verify failures.
- [ ] Implement `TTS_PROVIDER=auto` routing and cost recording.
- [ ] Run TTS tests and commit.

### Task 4: Two-candidate Veo selection and permanent reuse

**Files:**
- Modify: `app/services/vertex_video.py`
- Modify: `app/services/ai_opening_library.py`
- Modify: `app/agents/story_producer.py`
- Modify: `tests/test_vertex_video.py`
- Modify: `tests/test_ai_opening_library.py`
- Modify: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: credit guard.
- Produces: current-price model cost metadata and up to two independently stored candidates.
- Selection score: validation pass first, then lower reference-frame distance, then higher usable brightness.

- [ ] Write failing tests for current Veo price, two candidates, best-candidate selection, free-mode no-generation, and ready-asset reuse.
- [ ] Run targeted tests and verify failures.
- [ ] Implement minimal candidate orchestration and preserve every candidate metadata record.
- [ ] Run media tests and commit.

### Task 5: Storage and mode notifications

**Files:**
- Create: `app/services/ai_storage_monitor.py`
- Modify: `scripts/run_scheduled.py`
- Create: `tests/test_ai_storage_monitor.py`
- Modify: `tests/test_notifications.py`

**Interfaces:**
- Produces: `storage_status(data_dir: Path) -> dict` with disk and AI-library usage.
- Sends a deduplicated alert at 75% disk use, 10GB library size, or premium-to-free transition.

- [ ] Write failing threshold and deduplication tests.
- [ ] Run targeted tests and verify failures.
- [ ] Implement read-only monitoring; never delete AI assets.
- [ ] Run tests and commit.

### Task 6: Full verification, deployment, and 17:00 replacement

**Files:**
- Modify: `README.md`
- Modify: `docs/OPERATIONS.md`

- [ ] Run the full local test suite with a workspace-local pytest temp directory.
- [ ] Commit documentation and push `main`.
- [ ] Back up the server application, `.env`, database, crontab, and `data/work/20260803-2`.
- [ ] Deploy the exact committed archive and update only non-secret premium/credit environment keys.
- [ ] Run the full server test suite and restart the dashboard.
- [ ] Hold the old 17:00 package, regenerate slot 2 in staging, and run the full upload-package quality gate.
- [ ] Promote only the passing package and run the slot-2 uploader; otherwise restore the backed-up package and report the blocker.
