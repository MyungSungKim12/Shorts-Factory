# Pipeline Hardening and Slot Prebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound free-media resource usage, serialize all scheduled runs, reject defective videos before YouTube, clean abandoned temporary data, and promote a validated test video into the next scheduled slot.

**Architecture:** Keep orchestration and content generation unchanged while extracting resource limits into focused services. `media_library` performs bounded streaming, `process_runner` centralizes subprocess deadlines, `quality_gate` validates the complete upload package, `recovery` owns both slot and global locks, and `slot_prebuild` atomically promotes a validated staging directory into `work/{run_id}`.

**Tech Stack:** Python 3.12, requests, asyncio, pathlib, ffmpeg/ffprobe, SQLite, pytest, Linux cron/systemd.

## Global Constraints

- Do not modify, print, or commit `.env` or `credentials/`.
- Keep 11:00, 17:00, and 21:00 KST schedules and `DAILY_UPLOAD_LIMIT=3`.
- Keep the hard YouTube limit at six uploads per day.
- Default limits: video 80MiB, image 15MiB, connect 10s, read 30s.
- Default deadlines: shot FFmpeg 180s, final FFmpeg 900s, probe/QC 180s.
- Upload must remain fail-closed whenever quality or prior upload state is ambiguous.
- Delete only resolved direct children of the system temp directory whose names start with `shorts-factory-`.

---

### Task 1: Bounded media selection and streaming download

**Files:**
- Modify: `app/services/media_library.py`
- Modify: `tests/test_media_library.py`

**Interfaces:**
- Produces: `media_limit(media_type: str) -> int`
- Produces: `_download_candidate(candidate: MediaCandidate, output: Path) -> int`
- Changes metadata: adds `download_bytes` and `rejected_candidates`

- [ ] **Step 1: Write failing candidate-selection tests**

Add tests proving a 1080×1920 variant ranks above 2160×3840 and 720×1280 ranks above an undersized variant:

```python
def test_output_sized_variant_wins_over_4k():
    values = media_library._best_variant([
        {"link": "4k", "width": 2160, "height": 3840},
        {"link": "output", "width": 1080, "height": 1920},
    ], "link")
    assert values["link"] == "output"
```

- [ ] **Step 2: Run `python -m pytest tests/test_media_library.py -q` and confirm the 4K selection test fails**

- [ ] **Step 3: Implement target-size scoring**

Rank portrait variants that meet 720×1280 by smallest pixel excess over 1080×1920; prefer exact-or-smaller adequate variants over 4K. Preserve portrait and minimum-resolution priorities.

- [ ] **Step 4: Write failing streaming-limit tests**

Use a fake response exposing `headers` and `iter_content` and assert:

```python
with pytest.raises(media_library.MediaTooLarge):
    media_library._download_candidate(candidate("x", 1080, 1920), output)
assert not output.exists()
```

Cover both an oversized `Content-Length` rejected before iteration and an unknown-length stream that crosses the limit after multiple chunks.

- [ ] **Step 5: Run the focused tests and confirm failure because downloads use `response.content`**

- [ ] **Step 6: Implement bounded streaming**

Add `MediaTooLarge`, parse byte limits from environment with safe positive defaults, call `requests.get(..., stream=True, timeout=(connect, read))`, write to `output.with_suffix(output.suffix + ".part")`, and atomically replace only after success. Delete the partial file in every exception path and return written bytes.

- [ ] **Step 7: Record resource metadata and run tests**

Count rejected oversized candidates in `fetch_story_media`, include actual bytes for the chosen file, then run:

```powershell
python -m pytest tests/test_media_library.py tests/test_story_producer.py -q
git diff --check
```

- [ ] **Step 8: Commit**

```powershell
git add app/services/media_library.py tests/test_media_library.py
git commit -m "fix: bound free media downloads"
```

### Task 2: Subprocess deadlines and safe temporary cleanup

**Files:**
- Create: `app/services/process_runner.py`
- Create: `app/services/temp_cleanup.py`
- Create: `tests/test_process_runner.py`
- Create: `tests/test_temp_cleanup.py`
- Modify: `app/agents/story_producer.py`
- Modify: `app/services/media_probe.py`
- Modify: `scripts/run_scheduled.py`

**Interfaces:**
- Produces: `run_checked(command: list[str], *, timeout: int, cwd: Path | None = None, text: bool = False) -> subprocess.CompletedProcess`
- Produces: `cleanup_stale_temp_dirs(now: datetime | None = None, max_age_seconds: int = 21600) -> dict`

- [ ] **Step 1: Write failing deadline tests**

Patch `subprocess.run` to raise `subprocess.TimeoutExpired` and assert `run_checked` raises `RuntimeError` containing the executable and timeout but not environment values.

- [ ] **Step 2: Verify the new service import fails**

Run `python -m pytest tests/test_process_runner.py -q` and confirm `ModuleNotFoundError`.

- [ ] **Step 3: Implement the checked runner and route story/probe commands through it**

Use `SHOT_FFMPEG_TIMEOUT_SEC=180`, `FINAL_FFMPEG_TIMEOUT_SEC=900`, and `MEDIA_PROBE_TIMEOUT_SEC=180`. `_run_ffmpeg` accepts a `timeout` argument; final overlay/concat calls pass the final timeout while shot and narration calls use the shot timeout.

- [ ] **Step 4: Write failing temp cleanup tests**

Create old `shorts-factory-old`, new `shorts-factory-active`, and old `unrelated` directories under a patched temp root. Assert only the old prefixed direct child is removed and returned counts/bytes are exact.

- [ ] **Step 5: Implement cleanup and prefixed producer temp directories**

Use `tempfile.TemporaryDirectory(prefix="shorts-factory-")`. Resolve the temp root and candidate, require `candidate.parent == temp_root`, require the prefix, check age, and use `shutil.rmtree(candidate)` only after those checks.

- [ ] **Step 6: Invoke cleanup at scheduled startup and run tests**

Print only the count and byte total from `scripts/run_scheduled.py`. Run:

```powershell
python -m pytest tests/test_process_runner.py tests/test_temp_cleanup.py tests/test_story_producer.py tests/test_media_probe.py -q
git diff --check
```

- [ ] **Step 7: Commit**

```powershell
git add app/services/process_runner.py app/services/temp_cleanup.py app/agents/story_producer.py app/services/media_probe.py scripts/run_scheduled.py tests/test_process_runner.py tests/test_temp_cleanup.py
git commit -m "feat: enforce media process deadlines and cleanup"
```

### Task 3: Cross-slot global pipeline lock

**Files:**
- Modify: `app/services/recovery.py`
- Modify: `tests/test_recovery.py`

**Interfaces:**
- Produces: `acquire_global_lock(path: Path, run_id: str, now: datetime) -> bool`
- Produces: `release_owned_lock(path: Path, run_id: str, pid: int) -> None`
- Extends: `run_with_recovery(..., lock_wait_seconds: int = 5400, lock_poll_seconds: int = 30)`

- [ ] **Step 1: Write failing global-lock tests**

Cover: a living PID owned by another slot causes injected sleep/poll; a dead PID lock is atomically replaced; timeout raises `RuntimeError`; cleanup removes only a lock whose JSON still matches the current PID and run_id.

- [ ] **Step 2: Run `python -m pytest tests/test_recovery.py -q` and confirm missing global-lock behavior**

- [ ] **Step 3: Implement JSON lock ownership**

Write `{"pid": os.getpid(), "run_id": run_id, "started_at": now.isoformat()}` with `O_CREAT|O_EXCL`. Parse legacy integer slot locks separately. Treat unreadable global locks as ambiguous and do not remove them automatically.

- [ ] **Step 4: Wait before the first pipeline attempt**

Acquire `data/recovery/pipeline.lock` after the exact-slot lock. Poll living owners until `PIPELINE_LOCK_WAIT_SECONDS` expires. Recheck ownership before unlinking in `finally`.

- [ ] **Step 5: Run recovery and full focused tests**

```powershell
python -m pytest tests/test_recovery.py tests/test_cache_and_slots.py -q
git diff --check
```

- [ ] **Step 6: Commit**

```powershell
git add app/services/recovery.py tests/test_recovery.py
git commit -m "feat: serialize scheduled pipeline slots"
```

### Task 4: Fail-closed upload package quality gate

**Files:**
- Create: `app/services/quality_gate.py`
- Create: `tests/test_quality_gate.py`
- Modify: `app/services/media_probe.py`
- Modify: `app/agents/uploader.py`
- Modify: `app/agents/orchestrator.py`

**Interfaces:**
- Produces: `validate_upload_package(work_dir: Path, ffmpeg_path: str) -> dict`
- Extends probe report: `audio_duration`, `duration_delta`, `internal_silence_max`
- Persists: `produce_log["quality_gate"] = {"passed": bool, "failures": list[str], "report": dict}`

- [ ] **Step 1: Write failing pure quality-gate tests**

Build script/produce fixtures and injected probe reports. Assert individual failures for `script_hash`, `intro_text`, `cta_duplicate`, `cta_text`, `audio_duration_delta`, and `internal_silence`, plus one complete passing package.

- [ ] **Step 2: Verify `quality_gate` import fails**

Run `python -m pytest tests/test_quality_gate.py -q` and confirm `ModuleNotFoundError`.

- [ ] **Step 3: Extend media probing**

Read video/audio stream durations separately. Run `silencedetect=noise=-45dB:d=1.2`, parse silence intervals, ignore intervals entirely within the first or final 0.25 seconds, and report the maximum remaining interval. Keep black detection and command deadline behavior.

- [ ] **Step 4: Implement package validation and atomic log update**

Reuse `validate_sample`, compute SHA-256 from exact `script.json` bytes, normalize only terminal `?`, `!`, `。`, compare CTA plan state, and atomically replace `produce_log.json` after adding the report. Raise `RuntimeError("업로드 품질검사 실패: ...")` when failures are non-empty.

- [ ] **Step 5: Gate the uploader before credentials or YouTube client creation**

Call `validate_upload_package` after duplicate/quota/fact checks but before `_get_youtube_client`. Return the report in the uploader result and copy the summary into the orchestrator run log.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest tests/test_quality_gate.py tests/test_media_probe.py tests/test_story_producer.py tests/test_cache_and_slots.py -q
git diff --check
```

- [ ] **Step 7: Commit**

```powershell
git add app/services/quality_gate.py app/services/media_probe.py app/agents/uploader.py app/agents/orchestrator.py tests/test_quality_gate.py tests/test_media_probe.py
git commit -m "feat: block defective uploads with quality gate"
```

### Task 5: Validate and promote a test video into the next slot

**Files:**
- Create: `app/services/slot_prebuild.py`
- Create: `scripts/prepare_next_slot.py`
- Create: `tests/test_slot_prebuild.py`
- Modify: `app/agents/orchestrator.py`

**Interfaces:**
- Produces: `next_scheduled_slot(now: datetime) -> tuple[str, datetime]`
- Produces: `promote_staging(data_dir: Path, staging_id: str, run_id: str, quality: dict) -> Path`
- Produces CLI: `python scripts/prepare_next_slot.py`

- [ ] **Step 1: Write failing next-slot tests**

Assert KST mappings: 10:00→today slot 1, 12:00→slot 2, 18:00→slot 3, 22:00→tomorrow slot 1. Assert exactly-at-slot time selects the following slot so a build cannot race the active cron.

- [ ] **Step 2: Write failing promotion safety tests**

Require all four files, a passing quality report, no uploaded DB row, no existing complete destination, and matching script/produce hash. Assert failed staging remains untouched and successful promotion produces `prepared.json` plus an atomically renamed work directory.

- [ ] **Step 3: Run `python -m pytest tests/test_slot_prebuild.py -q` and confirm the module is missing**

- [ ] **Step 4: Implement KST selection and atomic promotion**

Use `zoneinfo.ZoneInfo("Asia/Seoul")`, slots `{1: 11, 2: 17, 3: 21}`, and a sibling temporary destination named `.{run_id}.promoting-{pid}`. Copy validated files there, write hashes and schedule metadata, then rename only when destination is absent.

- [ ] **Step 5: Implement the prebuild CLI**

Generate researcher/writer/producer outputs under `data/staging/{staging_id}`, call the same upload package quality logic without contacting YouTube, compute the next slot after generation completes, and promote only when that future slot remains future at commit time.

- [ ] **Step 6: Make orchestrator preserve prepared metadata**

When `prepared.json` exists, record it in the run log, reuse valid topic/script/output by existing hashes, then let the ordinary uploader rerun the quality gate at cron time.

- [ ] **Step 7: Run tests and commit**

```powershell
python -m pytest tests/test_slot_prebuild.py tests/test_story_prompts.py tests/test_quality_gate.py -q
git diff --check
git add app/services/slot_prebuild.py scripts/prepare_next_slot.py app/agents/orchestrator.py tests/test_slot_prebuild.py
git commit -m "feat: prepare validated video for next slot"
```

### Task 6: Full verification, deployment, and supervised next-slot upload

**Files:**
- All modified backend files
- Remote `/home/ubuntu/shorts-factory-be`

- [ ] **Step 1: Run complete local verification**

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, only intended commits.

- [ ] **Step 2: Merge the feature branch into `main`, rerun the full suite, and push**

Verify local `HEAD` equals `origin/main` after push and neither `.env` nor `credentials/` appears in the commit list.

- [ ] **Step 3: Back up the remote deployment**

Back up `app/`, `scripts/`, `tests/`, crontab, `.env`, `credentials/`, `data/videos.sqlite`, `data/recovery`, and the target `data/work`/`data/staging` directories to a timestamped `/home/ubuntu/backups/shorts-factory-*` directory.

- [ ] **Step 4: Deploy and verify without uploading**

Copy tracked code, run remote `venv/bin/python -m pytest -q`, restart `shorts-dashboard`, and verify cron, `/api/health`, no active pipeline, and write access to recovery/staging directories.

- [ ] **Step 5: Generate one supervised staging video**

Run `venv/bin/python -u scripts/prepare_next_slot.py`, monitor resource use, and verify the selected downloads remain under configured limits, final QC passes, temp directories are cleaned, and exactly one future `prepared.json` exists.

- [ ] **Step 6: Verify the scheduled upload**

At the target cron time confirm the prepared run skips generation, reruns QC, uploads exactly once, records one public video ID in SQLite, releases both locks, and appears in the monitoring API. Return the Shorts URL and backup path.
