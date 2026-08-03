# Story 90-Second Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every generated story Short at 70–80 seconds where possible and never above 90 seconds.

**Architecture:** Enforce a compact narration contract before saving `script.json`, then use the measured TTS duration to apply only a bounded natural speed-up. A shared 90-second configuration remains the final producer, quality-gate, and uploader boundary.

**Tech Stack:** Python 3.12, Pydantic v2, FFmpeg `atempo`, pytest, python-dotenv

## Global Constraints

- Story body narration is at most 400 normalized characters.
- Each story scene narration is at most 55 normalized characters.
- Final video target is 70–80 seconds and the hard maximum is 90 seconds.
- Gemini TTS uses the youthful `Leda` voice at 1.2x, preserves pitch, is never slowed down, and is never accelerated beyond 1.2x.
- Do not change topic selection, media selection, Veo, captions, schedules, credit handling, or unrelated agent behavior.

---

### Task 1: Enforce the authoring budget

**Files:**
- Modify: `app/models.py`
- Modify: `app/agents/writer.py`
- Modify: `agents/02_script-writer.md`
- Test: `tests/test_story_contracts.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: `validate_script(data, "story")`
- Produces: a validated `StoryScriptContract` whose scene narration is within the 55/400 character limits

- [ ] **Step 1: Write failing contract tests**

Add literal fixtures proving that one 56-character scene and a story body over 400 characters are rejected, while boundary-sized narration is accepted.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\venv\Scripts\python.exe -m pytest tests\test_story_contracts.py -q --basetemp data\pytest-story-limit-red`

Expected: the over-budget inputs are currently accepted.

- [ ] **Step 3: Implement the minimal validators and prompt limits**

Normalize internal whitespace before counting. Add a per-scene maximum of 55 characters and a total body maximum of 400 characters to the story contract. Update only the story writer prompt and its role document to state the same limits and the 70–80 second target.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.\venv\Scripts\python.exe -m pytest tests\test_story_contracts.py tests\test_story_prompts.py -q --basetemp data\pytest-story-limit-green`

Expected: all selected tests pass.

### Task 2: Bound measured TTS to 90 seconds

**Files:**
- Modify: `app/agents/story_producer.py`
- Modify: `app/services/tts.py`
- Modify: `app/services/media_probe.py`
- Modify: `app/agents/producer.py`
- Modify: `app/agents/uploader.py`
- Modify: `.env.example`
- Test: `tests/test_story_producer.py`
- Test: `tests/test_tts_premium.py`
- Test: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: `story_tempo_adjustment(intro_audio_duration, body_audio_duration, cta_audio_duration, scene_count)`
- Produces: an `atempo` factor of `1.2`, or raises when the final video cannot fit within 90 seconds at that speed

- [ ] **Step 1: Write failing tempo-boundary tests**

Use hand-calculated durations to prove: normal audio returns 1.2x without slowdown and audio that still exceeds 90 seconds at 1.2x raises `RuntimeError`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\venv\Scripts\python.exe -m pytest tests\test_story_producer.py -q --basetemp data\pytest-tempo-red`

Expected: the current function returns 1.0 for all over-90-second inputs.

- [ ] **Step 3: Implement bounded speed-up and shared ceiling**

Apply the fixed 1.2x factor through the existing pitch-preserving `_retime_audio`, remove the old slowdown-to-60-seconds behavior, and reject audio that remains over 90 seconds after that adjustment. Change the Gemini TTS voice from `Kore` to youthful `Leda` and remove the calm/long-pause style in favor of natural conversational pacing with short sentence-boundary pauses. Set the default and documented `MAX_VIDEO_SEC` to 90 so producer, quality gate, and uploader share one boundary.

- [ ] **Step 4: Run focused and full tests**

Run: `.\venv\Scripts\python.exe -m pytest tests\test_story_producer.py tests\test_quality_gate.py -q --basetemp data\pytest-tempo-green`

Run: `.\venv\Scripts\python.exe -m pytest -q --basetemp data\pytest-story-90-full`

Expected: all tests pass.

### Task 3: Deploy before the 21:00 slot

**Files:**
- Modify on server only: `/home/ubuntu/shorts-factory-be/.env` (`MAX_VIDEO_SEC=90`)
- Regenerate: `/home/ubuntu/shorts-factory-be/data/work/20260803-3`

**Interfaces:**
- Consumes: committed source archive and server secrets already present in `.env`
- Produces: a quality-passed 21:00 upload package whose measured duration is at most 90 seconds

- [ ] **Step 1: Commit and push the scoped source changes**

Stage only the files listed in Tasks 1–2, commit with a Korean message, and push `main`.

- [ ] **Step 2: Hold the 21:00 cron and deploy the exact commit archive**

Temporarily comment only the slot-3 cron line, archive any existing `20260803-3` package recoverably, extract the commit archive, set `MAX_VIDEO_SEC=90`, and restart the dashboard.

- [ ] **Step 3: Run server tests**

Run: `venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 4: Generate and validate the 21:00 package**

Run: `venv/bin/python -u scripts/prepare_next_slot.py --slot 3`

Verify `prepared.json.quality_gate.passed == true` and `produce_log.json.actual_duration <= 90`.

- [ ] **Step 5: Restore the cron or upload manually if 21:00 has passed**

Restore the slot-3 cron after a valid package exists. If generation finishes after 21:00, promote the quality-passed package and run `venv/bin/python -u scripts/run_scheduled.py 3` manually.
