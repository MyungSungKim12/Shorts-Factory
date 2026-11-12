# Free Grounding and Local TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep four scheduled Shorts producing after Gemini quota exhaustion and replace the unstable male-sounding fallback with a free local Korean female voice.

**Architecture:** Add a two-stage Groq browser-search grounding adapter behind the existing provider chain, retry duplicate grounded topics at the researcher boundary, and use a process-cached Supertonic 3 engine for TTS. Preserve all existing contracts and fail closed on TTS engine errors.

**Tech Stack:** Python, requests, pytest, Supertonic 3, ONNX Runtime, Linux cron

**Spec:** `docs/superpowers/specs/2026-11-12-free-grounding-and-tts.md`

## Global Constraints

- Keep the four existing cron upload slots unchanged.
- Keep the existing duplicate-topic rejection rules.
- Record `verification_method` on every topic.
- Do not commit `.env` or credentials.
- Never silently fall back to gTTS from Supertonic.

## Review Focus

- Gemini daily quota failure must reach Groq browser search.
- Groq research notes must be converted into valid JSON without combining JSON mode and tools.
- Three duplicate grounded candidates must fall through to the existing cache/model-memory path.
- Supertonic must initialize once per process and use Korean F3.
- Supertonic failure must stop production instead of producing gTTS audio.

---

### Task 1: Groq browser-search grounding fallback

**Files:**
- Modify: `app/services/claude_client.py`
- Test: `tests/test_claude_client_grounding.py`

**Interfaces:**
- Produces: `_groq_grounded_call(prompt: str, max_tokens: int, agent_name: str) -> str`

- [ ] Write failing tests for provider fallback and the two-stage search-to-JSON flow.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the minimal Groq browser-search adapter and provider-chain entry.
- [ ] Run focused tests and confirm they pass.

### Task 2: Duplicate grounded-topic reselection

**Files:**
- Modify: `app/agents/researcher.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: existing `call_agent(..., grounded=True)` and `_validate_candidate()`.
- Produces: up to three grounded selections with rejected selections carried forward.

- [ ] Write a failing test where the first grounded candidate is duplicate and the second is fresh.
- [ ] Run the focused test and confirm it fails.
- [ ] Implement bounded grounded reselection without weakening duplicate checks.
- [ ] Run focused tests and confirm they pass.

### Task 3: Free local Korean female TTS

**Files:**
- Modify: `app/services/tts.py`
- Modify: `app/agents/story_producer.py`
- Modify: `.env.example`
- Modify: `requirements.txt`
- Modify: `agents/03_video-producer.md`
- Modify: `README.md`
- Test: `tests/test_tts.py`
- Test: `tests/test_tts_premium.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `_synthesize_supertonic(text: str, output: Path, voice: str, speed: float) -> None`.
- Produces: `TTS_PROVIDER=supertonic`, `SUPERTONIC_VOICE=F3`, `SUPERTONIC_SPEED=1.0`.

- [ ] Replace Edge expectations with failing Supertonic expectations.
- [ ] Run focused tests and confirm they fail.
- [ ] Implement one-per-process Supertonic engine reuse and fail-closed behavior.
- [ ] Run focused tests and confirm they pass.

### Task 4: Verification and deployment

**Files:**
- Modify on server: `/home/ubuntu/shorts-factory-be/.env` without printing secrets.

- [ ] Run the complete local pytest suite.
- [ ] Deploy only tracked application files and install pinned dependencies.
- [ ] Pre-download Supertonic model assets and synthesize one disposable probe.
- [ ] Restart the API service and verify health.
- [ ] Run server focused tests and a no-upload grounded-research probe.
- [ ] Verify crontab remains unchanged and no production upload was triggered.
