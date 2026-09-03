# Longform Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate longform generator that creates previewable 5~10 minute videos while preserving the existing Shorts automation.

**Architecture:** Add focused longform contracts, a renderer module, and a CLI script. Reuse existing TTS, media, FFmpeg helpers, and the permanent AI opening library. Longform artifacts live in `data/longform/{run_id}` and are not uploaded automatically.

**Tech Stack:** Python, Pydantic, FFmpeg, Google TTS, existing media/AI asset services.

**Spec:** `docs/superpowers/specs/2026-09-03-longform-pipeline-design.md`

## Global Constraints

- Do not change the Shorts daily automation schedule.
- Do not commit `.env`, `credentials/`, or generated media.
- Longform output must be file-first and manual-review-first.
- AI assets must remain reusable after credits expire.

---

### Task 1: Longform contracts

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_longform_models.py`

**Interfaces:**
- Produces: `validate_longform_script(data: dict) -> dict`

- [ ] Write failing tests for valid and invalid longform scripts.
- [ ] Run the tests and confirm they fail because the validator does not exist.
- [ ] Add `LongformScene`, `LongformScriptContract`, and `validate_longform_script`.
- [ ] Run the tests and confirm they pass.

### Task 2: Longform renderer

**Files:**
- Create: `app/agents/longform_producer.py`
- Test: `tests/test_longform_producer.py`

**Interfaces:**
- Consumes: `validate_longform_script`
- Produces: `run_longform_producer(data_dir: Path, run_id: str, ffmpeg_path: str) -> dict`

- [ ] Write failing tests for output paths, AI reuse metadata, and no Shorts work-dir mutation.
- [ ] Run the tests and confirm they fail because the producer does not exist.
- [ ] Implement a minimal renderer that reads `data/longform/{run_id}/script.json`, creates a deterministic preview MP4 from title card/slides/narration, and records `produce_log.json`.
- [ ] Run the tests and confirm they pass.

### Task 3: CLI entrypoint and server file visibility

**Files:**
- Create: `scripts/generate_longform.py`
- Modify: `app/services/server_files.py`
- Test: `tests/test_generate_longform.py`, `tests/test_server_files_api.py`

**Interfaces:**
- Consumes: `run_longform_producer`
- Produces: CLI command `python scripts/generate_longform.py --run-id longform-demo`

- [ ] Write failing tests for CLI input/output and server file category visibility.
- [ ] Run the tests and confirm they fail.
- [ ] Add the CLI and expose `data/longform` as a read-only server files category.
- [ ] Run focused tests and then the full suite.

