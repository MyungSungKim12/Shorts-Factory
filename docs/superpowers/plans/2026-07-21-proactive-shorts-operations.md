# Proactive Shorts Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 무료 소스의 화면 관련성을 강제하고, 11/17/21시 영상을 두 시간 전에 제작하며, 검증 소재를 매일 축적하고, 운영 결과를 Telegram으로 안전하게 알린다.

**Architecture:** 시각 식별·Telegram·캐시 워밍을 각각 독립 서비스로 두고 기존 에이전트는 작은 인터페이스만 호출한다. 사전 제작은 명시된 회차의 staging에서 끝까지 검증한 패키지만 work로 원자 승격하며, 기존 정시 파이프라인은 준비본이 없을 때의 최후 대안으로 유지한다.

**Tech Stack:** Python 3.12, Pydantic, requests, SQLite, FFmpeg/ffprobe, pytest, Linux cron, Telegram Bot HTTP API

## Global Constraints

- 오늘 `20260721-2` 17시 검증 완료 영상은 변경하지 않는다.
- `.env`, `credentials/`, Telegram Bot Token은 Git에 커밋하거나 로그에 출력하지 않는다.
- YouTube 업로드는 하루 최대 6건이며 운영 스케줄은 11:00·17:00·21:00 KST다.
- 캐시에는 `grounded_search`에 성공한 소재만 새로 적재한다. `model_memory`는 캐시 워밍에 사용하지 않는다.
- Telegram 장애는 제작·검증·업로드 결과를 실패로 바꾸지 않는다.
- 모든 파일 다운로드와 FFmpeg 실행은 기존 크기 제한·타임아웃·전역 잠금을 유지한다.

---

### Task 1: Story Visual Identity Contract

**Files:**
- Create: `app/services/visual_relevance.py`
- Modify: `app/models.py`
- Modify: `app/agents/researcher.py`
- Modify: `app/agents/writer.py`
- Test: `tests/test_visual_relevance.py`
- Test: `tests/test_story_contracts.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Produces: `VisualIdentity`, `ensure_visual_identity(topic: dict) -> dict`, `story_scene_queries(script: dict, topic: dict) -> dict[int, list[str]]`
- Consumes: story topic fields `topic`, `target_keyword`, `category`, `visual_plan`

- [ ] **Step 1: Write failing identity and prompt tests**

```python
def test_missing_visual_identity_is_derived_from_verified_topic():
    topic = validate_topic(_story_topic(), "story")
    identity = topic["visual_identity"]
    assert identity["exact_queries"][0].startswith("exact:")
    assert identity["safe_fallbacks"]
    assert identity["required_exact"] is True

def test_hook_and_close_queries_keep_subject_anchor():
    queries = story_scene_queries(_script(), _story_topic())
    assert queries[1][0].startswith("exact:")
    assert queries[len(_script()["scenes"])][0].startswith("exact:")
```

- [ ] **Step 2: Run tests and verify contract is absent**

Run: `python -m pytest tests/test_visual_relevance.py tests/test_story_contracts.py tests/test_story_prompts.py -q`

Expected: FAIL because `VisualIdentity` and `visual_relevance` do not exist.

- [ ] **Step 3: Add the visual identity model and deterministic derivation**

```python
class VisualIdentity(BaseModel):
    exact_queries: list[str] = Field(min_length=1, max_length=3)
    safe_fallbacks: list[str] = Field(min_length=1, max_length=5)
    required_exact: bool = True

class StoryTopicContract(BaseModel):
    # existing fields remain unchanged
    visual_identity: VisualIdentity | None = None

def validate_topic(data: dict, content_format: str | None = None) -> dict:
    selected = content_format or data.get("format") or "ranking"
    model = StoryTopicContract if selected == "story" else TopicContract
    result = model.model_validate(data).model_dump()
    return ensure_visual_identity(result) if selected == "story" else result
```

`ensure_visual_identity` derives missing values from `target_keyword`, `topic`, and `visual_plan` without introducing facts. `story_scene_queries` returns a new mapping without rewriting `script.json`; hook and close begin with exact anchors, while middle scenes use their original concrete queries followed only by `safe_fallbacks`.

- [ ] **Step 4: Update researcher/writer prompts to request the same fields**

Require `visual_identity` in the requested JSON, explain that `safe_fallbacks` must stay in the same real-world subject family, and keep deterministic derivation for legacy/cached topics.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_visual_relevance.py tests/test_story_contracts.py tests/test_story_prompts.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/models.py app/services/visual_relevance.py app/agents/researcher.py app/agents/writer.py tests/test_visual_relevance.py tests/test_story_contracts.py tests/test_story_prompts.py
git commit -m "feat: anchor story visuals to verified subjects"
```

### Task 2: Strict Exact Media and Visual Quality Gate

**Files:**
- Modify: `app/services/media_library.py`
- Modify: `app/agents/story_producer.py`
- Modify: `app/services/quality_gate.py`
- Test: `tests/test_media_library.py`
- Test: `tests/test_story_producer.py`
- Test: `tests/test_quality_gate.py`

**Interfaces:**
- Produces: `exact_candidate_matches(query: str, candidate: MediaCandidate) -> bool`, `fetch_required_exact_media(identity: dict, destination: Path, used_ids: set[str]) -> tuple[Path, dict]`, `produce_log.visual_relevance`
- Consumes: Task 1 `story_scene_queries` and topic `visual_identity`

- [ ] **Step 1: Write failing strict-match and quality tests**

```python
def test_exact_candidate_rejects_unrelated_title():
    moon = MediaCandidate(
        provider="wikimedia_image", media_id="File:Moon surface.jpg",
        source_url="x", download_url="x", width=1200, height=1600,
        media_type="image", keyword="Richat Structure Mauritania",
    )
    assert exact_candidate_matches("Richat Structure Mauritania", moon) is False

def test_quality_gate_rejects_missing_required_exact_source(package):
    package.produce_log["visual_relevance"] = {
        "required_exact": True, "exact_source_count": 0,
        "generic_fallback_count": 4, "unrelated_fallback_count": 0,
    }
    with pytest.raises(RuntimeError, match="visual_exact_source"):
        validate_upload_package(package.path, "ffmpeg")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_media_library.py tests/test_story_producer.py tests/test_quality_gate.py -q`

Expected: FAIL because strict matching and visual quality fields are missing.

- [ ] **Step 3: Implement strict Wikimedia matching**

Normalize query and candidate title to lowercase alphanumeric tokens, discard generic tokens (`file`, `image`, `photo`, `structure`, `landscape`), and require at least one remaining distinctive token overlap. Apply this predicate only to `exact:` Wikimedia candidates; stock providers keep their existing ranked selection.

- [ ] **Step 4: Reserve one real subject asset before shot rendering**

At producer startup, call `fetch_required_exact_media` when `required_exact` is true. Fail before multi-shot rendering if no licensed matching image exists. Reuse the downloaded exact asset for the first hook shot and allow it again for the close visual without a second download.

Record:

```python
produce_log["visual_relevance"] = {
    "required_exact": identity["required_exact"],
    "exact_source_count": exact_count,
    "generic_fallback_count": generic_count,
    "unrelated_fallback_count": 0,
    "queries": used_queries,
}
```

- [ ] **Step 5: Extend upload quality gate**

Append `visual_exact_source` if required and count is zero; append `visual_unrelated_fallback` if unrelated count is nonzero. Persist failures in the existing atomic `quality_gate` object before raising.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m pytest tests/test_media_library.py tests/test_story_producer.py tests/test_quality_gate.py -q`

Expected: PASS.

```powershell
git add app/services/media_library.py app/agents/story_producer.py app/services/quality_gate.py tests/test_media_library.py tests/test_story_producer.py tests/test_quality_gate.py
git commit -m "feat: reject unrelated story media"
```

### Task 3: Deterministic Scheduled Prebuild

**Files:**
- Modify: `app/services/slot_prebuild.py`
- Modify: `scripts/prepare_next_slot.py`
- Modify: `app/agents/orchestrator.py`
- Test: `tests/test_slot_prebuild.py`
- Test: `tests/test_story_prompts.py`

**Interfaces:**
- Produces: `scheduled_run(now: datetime, slot: int) -> tuple[str, datetime]`, `prepare_slot(data_dir: Path, ffmpeg_path: str, slot: int, *, now_fn: Callable[[], datetime] | None = None, use_lock: bool = True) -> dict`
- Preserves: `prepare_next_slot(...)` for manual nearest-future use

- [ ] **Step 1: Write failing slot and fallback tests**

```python
@pytest.mark.parametrize("hour,slot,run_id", [
    (9, 1, "20260721-1"), (15, 2, "20260721-2"), (19, 3, "20260721-3")
])
def test_prebuild_targets_explicit_same_day_slot(hour, slot, run_id):
    assert scheduled_run(datetime(2026, 7, 21, hour, tzinfo=KST), slot)[0] == run_id

def test_expired_explicit_slot_is_rejected():
    with pytest.raises(RuntimeError, match="이미 지난"):
        scheduled_run(datetime(2026, 7, 21, 17, 1, tzinfo=KST), 2)
```

Also assert an existing passed `prepared.json` prevents regeneration and an absent marker leaves the current just-in-time orchestrator path unchanged.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_slot_prebuild.py tests/test_story_prompts.py -q`

Expected: FAIL because explicit scheduled prebuild is missing.

- [ ] **Step 3: Implement explicit target selection and CLI**

```python
def scheduled_run(now: datetime, slot: int) -> tuple[str, datetime]:
    schedule = {1: time(11), 2: time(17), 3: time(21)}
    scheduled_at = datetime.combine(now.astimezone(KST).date(), schedule[slot], tzinfo=KST)
    if scheduled_at <= now.astimezone(KST):
        raise RuntimeError(f"이미 지난 예약 회차: {slot}")
    return f"{scheduled_at:%Y%m%d}-{slot}", scheduled_at
```

Add `--slot {1,2,3}`. When provided, compute the target before work, keep the same target after rendering, and reject an existing destination or uploaded DB row. Without `--slot`, retain manual nearest-future behavior.

- [ ] **Step 4: Keep prepared reuse and just-in-time fallback**

The orchestrator must only label a package as prepared when `prepared.json.run_id` matches and its quality result passed. Existing valid `topic/script/output` reuse stays intact. When no prepared package exists, current generation behavior remains unchanged.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/test_slot_prebuild.py tests/test_story_prompts.py tests/test_recovery.py -q`

Expected: PASS.

```powershell
git add app/services/slot_prebuild.py scripts/prepare_next_slot.py app/agents/orchestrator.py tests/test_slot_prebuild.py tests/test_story_prompts.py
git commit -m "feat: prebuild explicit upload slots"
```

### Task 4: Free Grounded Cache Warmer

**Files:**
- Create: `app/services/cache_warmer.py`
- Create: `scripts/warm_verified_cache.py`
- Modify: `app/agents/researcher.py`
- Modify: `app/services/fact_cache.py`
- Test: `tests/test_cache_warmer.py`
- Test: `tests/test_cache_and_slots.py`

**Interfaces:**
- Produces: `warm_verified_cache(data_dir: Path, target_per_slot: int = 10, *, researcher: Callable[..., dict] | None = None, now: datetime | None = None) -> dict`
- Extends: `run_researcher(..., verification_policy: str = "normal")`; accepted values are `normal` and `grounded_only`

- [ ] **Step 1: Write failing policy and target tests**

```python
def test_full_slot_skips_grounded_call(tmp_path, monkeypatch):
    for slot in (1, 2, 3):
        seed_verified(tmp_path, slot=slot, count=10)
    calls = []
    result = warm_verified_cache(tmp_path, target_per_slot=10, researcher=lambda *a, **k: calls.append(k))
    assert 1 in result["skipped_full_slots"]
    assert not calls

def test_quota_exhaustion_stops_remaining_slots(tmp_path):
    result = warm_verified_cache(tmp_path, researcher=raise_daily_quota)
    assert result["quota_exhausted"] is True
    assert result["attempted_slots"] == [1]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_cache_warmer.py tests/test_cache_and_slots.py -q`

Expected: FAIL because warmer and grounded-only policy do not exist.

- [ ] **Step 3: Add grounded-only researcher policy**

When `verification_policy="grounded_only"`, accept and save only a validated `grounded_search` response. On provider failure or daily quota exhaustion, raise a typed `GroundingUnavailable` carrying `daily_quota: bool`; do not read verified cache and do not invoke model-memory conservative mode.

- [ ] **Step 4: Implement bounded cache warming**

For slots 1 through 3, skip when `cache_size(data_dir, slot) >= target_per_slot`; otherwise call grounded-only researcher once with a unique `cache-warm-YYYYMMDD-slot` ID and recent/cache topic exclusions. Stop all remaining work on daily quota exhaustion. Return counts and per-slot sizes without calling writer, producer, or uploader.

- [ ] **Step 5: Add CLI and run tests**

`scripts/warm_verified_cache.py` loads `.env`, calls the service once, prints a token-free JSON summary, and exits zero when quota is exhausted after safely recording the result.

Run: `python -m pytest tests/test_cache_warmer.py tests/test_cache_and_slots.py tests/test_story_prompts.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/cache_warmer.py scripts/warm_verified_cache.py app/agents/researcher.py app/services/fact_cache.py tests/test_cache_warmer.py tests/test_cache_and_slots.py
git commit -m "feat: warm grounded topic cache for free"
```

### Task 5: Failure-Isolated Telegram Alerts

**Files:**
- Create: `app/services/notifications.py`
- Modify: `scripts/prepare_next_slot.py`
- Modify: `scripts/run_scheduled.py`
- Modify: `scripts/warm_verified_cache.py`
- Modify: `.env.example`
- Test: `tests/test_notifications.py`
- Test: `tests/test_slot_prebuild.py`
- Test: `tests/test_recovery.py`
- Test: `tests/test_cache_warmer.py`

**Interfaces:**
- Produces: `send_alert(data_dir: Path, event_key: str, text: str, *, now: datetime | None = None) -> dict`, `safe_error(exc: Exception) -> str`
- State: `data/notifications/state.json`, atomically written; duplicate TTL 24 hours

- [ ] **Step 1: Write failing alert tests**

```python
def test_missing_credentials_is_disabled_without_http(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = send_alert(tmp_path, "upload:1", "ok")
    assert result == {"status": "disabled"}

def test_http_failure_never_raises(tmp_path, configured_env, monkeypatch):
    monkeypatch.setattr(requests, "post", raise_timeout)
    assert send_alert(tmp_path, "upload:1", "ok")["status"] == "error"

def test_duplicate_event_is_sent_once(tmp_path, configured_env, fake_post):
    send_alert(tmp_path, "upload:20260721-2:success", "first")
    result = send_alert(tmp_path, "upload:20260721-2:success", "second")
    assert result["status"] == "duplicate"
    assert fake_post.calls == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_notifications.py -q`

Expected: FAIL because notification service does not exist.

- [ ] **Step 3: Implement safe Telegram adapter**

POST to `https://api.telegram.org/bot{token}/sendMessage` with `chat_id`, plain text, `timeout=(5, 10)`. Never log the URL because it contains the token. `safe_error` replaces Bot API token-shaped strings and truncates to 300 characters. Catch all `requests.RequestException`, JSON errors, and state-file errors; return status without raising.

- [ ] **Step 4: Connect lifecycle events**

- `prepare_next_slot.py`: success and failure with run ID, title, duration, verification method
- `run_scheduled.py`: uploaded URL, skipped reason, recovery-exhausted stage/error
- `warm_verified_cache.py`: added counts, sizes, quota exhaustion/cache shortage
- recovery timeout: scheduler event key scoped by run ID

Every message is assembled from whitelisted fields, never full environment or raw HTTP responses.

- [ ] **Step 5: Document non-secret environment names and run tests**

Append disabled placeholders to `.env.example` without a real Chat ID or token.

Run: `python -m pytest tests/test_notifications.py tests/test_slot_prebuild.py tests/test_recovery.py tests/test_cache_warmer.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/notifications.py scripts/prepare_next_slot.py scripts/run_scheduled.py scripts/warm_verified_cache.py .env.example tests/test_notifications.py tests/test_slot_prebuild.py tests/test_recovery.py tests/test_cache_warmer.py
git commit -m "feat: send isolated pipeline alerts to telegram"
```

### Task 6: Full Verification, Cron Deployment, and Live Check

**Files:**
- Modify: `docs/OPERATIONS.md`
- Server-only: `/home/ubuntu/shorts-factory-be/.env` (never stage or print)
- Server-only: user crontab

**Interfaces:**
- Consumes all prior task interfaces
- Produces deployed 06:30/09:00/11:00/15:00/17:00/19:00/21:00 schedule

- [ ] **Step 1: Run fresh local verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q app scripts tests
git diff --check
git status --short
```

Expected: all tests pass, compile/diff exit 0, only intended documentation changes if any.

- [ ] **Step 2: Update operations documentation**

Document the seven cron entries, prebuild fallback behavior, cache target/expiry, Telegram environment names, token rotation via BotFather `/revoke`, and rollback to the three original upload entries.

- [ ] **Step 3: Commit and push main**

```powershell
git add docs/OPERATIONS.md
git commit -m "docs: operate proactive shorts schedule"
git push origin main
```

- [ ] **Step 4: Back up server before mutation**

Create a new timestamped directory under `/home/ubuntu/backups/` and copy `app`, `scripts`, `tests`, `config`, `.env`, `credentials`, `data`, and the current crontab. Verify backup size and required top-level entries without printing secret contents.

- [ ] **Step 5: Deploy and verify server code before cron changes**

Transfer only tracked Git content, preserve server `.env`, credentials, data, and venv, then run:

```bash
venv/bin/python -m pytest -q
venv/bin/python -m compileall -q app scripts
sudo systemctl restart shorts-dashboard
curl -fsS http://127.0.0.1:8000/api/health
```

Expected: remote suite passes, service is active, API returns `{"status":"ok","running":false}`.

- [ ] **Step 6: Configure Telegram secret interactively**

Use a no-echo prompt on the user's computer to write `TELEGRAM_BOT_TOKEN` to the server `.env` without placing the token in chat, Git, shell history, or command output. Set the confirmed Chat ID and enable flag in the same server file. Send one test message and verify `status=sent` without printing the token.

- [ ] **Step 7: Install and verify seven cron entries**

```cron
30 6 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/warm_verified_cache.py >> data/cron.log 2>&1
0 9 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/prepare_next_slot.py --slot 1 >> data/cron.log 2>&1
0 11 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_scheduled.py 1 >> data/cron.log 2>&1
0 15 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/prepare_next_slot.py --slot 2 >> data/cron.log 2>&1
0 17 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_scheduled.py 2 >> data/cron.log 2>&1
0 19 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/prepare_next_slot.py --slot 3 >> data/cron.log 2>&1
0 21 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_scheduled.py 3 >> data/cron.log 2>&1
```

Verify exactly seven project entries, no duplicates, and KST server time.

- [ ] **Step 8: Run a supervised next-slot prebuild**

Run the appropriate future slot manually, then verify `prepared.json.quality_gate.passed`, visual exact source count, 1080×1920 H.264/AAC, 60–75 seconds, audio delta ≤0.5 seconds, internal silence <1.2 seconds, no global lock, cleaned owned temp directory, and a Telegram success message.

- [ ] **Step 9: Verify the scheduled upload**

After its cron time, require one uploaded SQLite row for the run ID, a non-empty YouTube Shorts URL, uploader success in the run log, no remaining lock, healthy dashboard, and a Telegram upload-success message.

- [ ] **Step 10: Final security and repository check**

Run `git status -sb`, confirm local and `origin/main` hashes match, and use `git ls-files` to confirm `.env`, `credentials/`, token strings, generated videos, and server backups are not tracked.
