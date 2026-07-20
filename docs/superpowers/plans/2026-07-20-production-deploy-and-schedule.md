# Production Deploy and Three-Run Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the approved story pipeline to the Oracle server, verify one real YouTube upload, and schedule future runs at 11:00, 17:00, and 21:00 KST.

**Architecture:** Audit and back up remote mutable state before transferring source files. Verify server tests and configuration without printing secrets, perform one pipeline run with quota/duplicate protections intact, then atomically install a three-entry cron while preserving an exact rollback copy.

**Tech Stack:** OpenSSH/scp, Ubuntu cron, Python/pytest, SQLite, YouTube Data API v3.

## Global Constraints

- Never transfer or overwrite local `.env`, `credentials/`, `data/`, or `.git`.
- Keep the code-enforced maximum of six uploads per day; configure production for three scheduled runs.
- Use slots 1, 2, and 3 at 11:00, 17:00, and 21:00 Asia/Seoul time.
- Upload only after remote tests and video validation pass.
- Record `verification_method` and accept only uploader-approved methods.
- Preserve remote code, database, environment, OAuth credentials, and crontab backups before mutation.

---

### Task 1: Audit and back up production

**Files:**
- Remote read/write: `/home/ubuntu/shorts-factory-be`
- Remote backup: `/home/ubuntu/backups/shorts-cta-<timestamp>/`

**Interfaces:**
- Produces: timestamped backup directory and exact `crontab.before` rollback file.

- [ ] **Step 1: Read-only production audit**

Use SSH to report server time/timezone, current crontab, disk usage, git status/revision, dashboard status, non-secret values for `CONTENT_FORMAT`, `TTS_PROVIDER`, `UPLOAD_PRIVACY`, and `DAILY_UPLOAD_LIMIT`, and today's uploaded count from SQLite. Do not print API keys or token contents.

- [ ] **Step 2: Create timestamped backups**

Back up `app/`, `scripts/`, `config/`, `requirements.txt`, `README.md`, `.env`, `credentials/`, `data/videos.sqlite`, and `crontab -l` into one timestamped directory. Verify each expected entry exists before deployment.

### Task 2: Deploy source and verify the server

**Files:**
- Deploy: `app/`, `scripts/`, `config/`, `requirements.txt`, `README.md`
- Preserve: remote `.env`, `credentials/`, `data/`

**Interfaces:**
- Consumes: committed local branch source.
- Produces: tested production source with story CTA support.

- [ ] **Step 1: Transfer a source-only archive**

Create an archive containing only the deploy file list, copy it with scp, and extract it from `/home/ubuntu/shorts-factory-be`. Do not include ignored files.

- [ ] **Step 2: Install requirements and set non-secret runtime options**

Run `venv/bin/pip install -r requirements.txt`. Update only these remote `.env` keys while preserving all other lines: `CONTENT_FORMAT=story`, `TTS_PROVIDER=google`, `TTS_VOICE=ko-KR-Neural2-C`, `TTS_SPEAKING_RATE=1.05`, and `DAILY_UPLOAD_LIMIT=3`.

- [ ] **Step 3: Run remote verification**

Run: `venv/bin/python -m pytest -q`

Expected: all tests PASS.

Run a syntax import check and restart `shorts-dashboard`; require `systemctl is-active shorts-dashboard` to return `active`.

### Task 3: Generate and upload one real production Short

**Files:**
- Remote artifacts: `data/work/<YYYYMMDD-slot>/`
- Remote log: `data/logs/run-<YYYYMMDD-slot>.json`
- Remote DB: `data/videos.sqlite`

**Interfaces:**
- Consumes: next unused slot from today's SQLite records.
- Produces: one YouTube `video_id`, URL, privacy state, and DB row.

- [ ] **Step 1: Recheck quota and collision state**

Immediately before running, query today's uploaded count, inspect active pipeline processes, and choose an unused slot. Abort if another pipeline is active or the configured daily limit has been reached.

- [ ] **Step 2: Run one full pipeline**

Run `venv/bin/python -u scripts/run_daily.py <unused-slot>` and capture its exit code and run log. Do not retry the uploader with a different run ID if the first upload may have succeeded.

- [ ] **Step 3: Verify the upload**

Require all of the following: uploader status `uploaded`, non-empty `video_id`, matching DB row, output validation within 60–75 seconds, `verification_method` present, and a reachable YouTube Shorts URL. Report whether the configured privacy is public or unlisted.

### Task 4: Install the three-run cron

**Files:**
- Remote crontab

**Interfaces:**
- Consumes: the backed-up original crontab.
- Produces: exactly three Shorts pipeline entries while retaining unrelated cron entries.

- [ ] **Step 1: Build a replacement crontab non-interactively**

Remove only existing lines containing `/home/ubuntu/shorts-factory-be` and `scripts/run_daily.py`, preserve unrelated entries, and append:

```cron
0 11 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_daily.py 1 >> /home/ubuntu/shorts-factory-be/data/cron.log 2>&1
0 17 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_daily.py 2 >> /home/ubuntu/shorts-factory-be/data/cron.log 2>&1
0 21 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_daily.py 3 >> /home/ubuntu/shorts-factory-be/data/cron.log 2>&1
```

- [ ] **Step 2: Install and verify**

Apply the file with `crontab <file>`, then require `crontab -l` to contain exactly those three pipeline entries. Confirm the server timezone is `Asia/Seoul`; if not, prefix each entry with `CRON_TZ=Asia/Seoul` or convert times before installation.

- [ ] **Step 3: Record rollback instructions**

Record the backup directory and exact restore commands for source, `.env`, credentials, database, and crontab. Do not perform rollback unless verification fails or the user requests it.

