# Asset-first Longform Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a longform quality gate that produces longform videos only from confirmed, source-aware, visually relevant media and animates still images so scenes feel like video.

**Architecture:** Add a focused longform media preflight service that creates `media_board.json` before final rendering. Update the longform producer to consume the media board and render each scene with exact media, motion beats, and source-aware logging. Keep Shorts automation untouched.

**Tech Stack:** Python, FastAPI backend conventions, PIL, FFmpeg, pytest, existing media providers in `app.services.media_library`, existing AI cache in `app.services.ai_opening_library`.

**Spec:** `docs/superpowers/specs/2026-09-04-longform-asset-first-quality-design.md`

## Global Constraints

- Do not change the four-times-daily Shorts automation.
- Do not auto-upload longform videos.
- Store review artifacts under `data/longform/{run_id}/`.
- Preserve permanent AI assets and do not include them in seven-day cleanup.
- Use exact or strongly related media for core longform scenes.
- Generic stock can be used only as bridge footage, not as evidence for a named subject.
- Every media decision must be recorded in `media_board.json` or `produce_log.json`.

---

### Task 1: Longform media board model and quality gate

**Files:**
- Create: `app/services/longform_media_board.py`
- Test: `tests/test_longform_media_board.py`

**Interfaces:**
- Consumes: plain dictionaries from `script.json` or proposed topic data.
- Produces:
  - `media_tier_for_source(source: dict) -> str`
  - `scene_media_quality(scene: dict, assets: list[dict]) -> dict`
  - `longform_media_gate(media_board: dict) -> dict`

- [ ] **Step 1: Write failing tests**

```python
def test_exact_wikimedia_counts_as_tier_a():
    from app.services.longform_media_board import media_tier_for_source

    source = {
        "provider": "wikimedia_image",
        "exact_match": True,
        "source_url": "https://commons.wikimedia.org/wiki/File:Richat.jpg",
    }

    assert media_tier_for_source(source) == "A"


def test_generic_stock_cannot_pass_core_scene_alone():
    from app.services.longform_media_board import longform_media_gate

    board = {
        "run_id": "longform-demo",
        "scenes": [
            {
                "n": 1,
                "role": "hook",
                "duration_sec": 12,
                "assets": [{"tier": "D", "provider": "pexels_video"}],
            },
            {
                "n": 2,
                "role": "evidence",
                "duration_sec": 20,
                "assets": [{"tier": "D", "provider": "pixabay_video"}],
            },
        ],
    }

    result = longform_media_gate(board)

    assert result["passed"] is False
    assert "core scene lacks Tier A/C media" in result["reasons"][0]
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_longform_media_board.py -v`

- [ ] **Step 3: Implement minimal model and gate**

Create `app/services/longform_media_board.py` with deterministic tier rules:

```python
CORE_ROLES = {"hook", "evidence", "mechanism", "payoff"}


def media_tier_for_source(source: dict) -> str:
    provider = str(source.get("provider") or "").lower()
    if provider in {"wikimedia_image", "nasa_image"} and source.get("exact_match"):
        return "A"
    if source.get("asset_id") and source.get("source_reference"):
        return "C"
    if provider in {"wikimedia_image", "nasa_image", "pexels_video", "pexels_image", "pixabay_video"}:
        return "B" if source.get("strong_match") else "D"
    return "D"
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `pytest tests/test_longform_media_board.py -v`

---

### Task 2: Build media preflight from existing providers and cache

**Files:**
- Create: `app/services/longform_media_preflight.py`
- Modify: `scripts/generate_longform.py`
- Test: `tests/test_longform_media_preflight.py`

**Interfaces:**
- Consumes:
  - `prepare_longform_media_board(data_dir: Path, run_id: str) -> dict`
  - reads `data/longform/{run_id}/script.json`
- Produces:
  - `data/longform/{run_id}/media_board.json`
  - quality gate result inside the board.

- [ ] **Step 1: Write failing tests**

```python
def test_preflight_writes_media_board_for_script(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    run_dir = tmp_path / "longform" / "longform-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "script.json").write_text(
        '{"format":"longform","title":"사하라의 눈","hook":"왜 보일까?",'
        '"total_duration_sec":240,"style_id":"clean_news","tags":["미스터리"],'
        '"scenes":[{"n":1,"role":"hook","chapter_title":"위성사진의 눈",'
        '"narration":"사하라의 눈을 위에서 보면 거대한 고리처럼 보입니다.",'
        '"duration_sec":30,"visual_query":"exact: Richat Structure"}]}',
        encoding="utf-8",
    )

    def fake_candidates(query):
        from app.services.media_library import MediaCandidate
        return [
            MediaCandidate(
                provider="wikimedia_image",
                media_id="File:Richat.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:Richat.jpg",
                download_url="https://upload.wikimedia.org/richat.jpg",
                description="Richat Structure",
                license="CC BY-SA 4.0",
                media_type="image",
                exact_match=True,
            )
        ]

    monkeypatch.setattr(
        "app.services.longform_media_preflight._wikimedia_image_candidates",
        fake_candidates,
    )

    board = prepare_longform_media_board(tmp_path, "longform-demo")

    assert board["run_id"] == "longform-demo"
    assert board["scenes"][0]["assets"][0]["tier"] == "A"
    assert (run_dir / "media_board.json").is_file()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_longform_media_preflight.py -v`

- [ ] **Step 3: Implement preflight**

The preflight should:

- Read `script.json`.
- For each scene, use `visual_query`, `chapter_title`, and `visual_identity.exact_queries`.
- Prefer reusable AI assets from `AiOpeningLibrary`.
- Search Wikimedia/NASA/Pexels/Pixabay metadata through existing candidate functions.
- Do not download heavy media in this first preflight step.
- Save selected candidate metadata and gate result to `media_board.json`.

- [ ] **Step 4: Add CLI option**

Update `scripts/generate_longform.py`:

```text
--prepare-media
```

When present, run preflight only and print the `media_board.json` path.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
pytest tests/test_longform_media_board.py tests/test_longform_media_preflight.py tests/test_generate_longform.py -v
```

---

### Task 3: Generate contact sheet for operator review

**Files:**
- Modify: `app/services/longform_media_preflight.py`
- Test: `tests/test_longform_media_preflight.py`

**Interfaces:**
- Produces: `data/longform/{run_id}/media_contact_sheet.png`

- [ ] **Step 1: Write failing test**

```python
def test_preflight_creates_contact_sheet(tmp_path, monkeypatch):
    from app.services.longform_media_preflight import prepare_longform_media_board

    # Reuse a minimal script fixture with one scene and one fake exact candidate.
    # Assert media_contact_sheet.png exists after preflight.
```

- [ ] **Step 2: Implement contact sheet**

Use PIL to create a lightweight board with:

- Run ID and title.
- One row per scene.
- Scene number, role, chapter title.
- Best asset tier and provider.
- Source URL shortened enough for review.

This does not need to fetch thumbnails yet; it is a fast operator checklist.

- [ ] **Step 3: Run preflight tests**

Run: `pytest tests/test_longform_media_preflight.py -v`

---

### Task 4: Materialize selected media-board assets

**Files:**
- Modify: `app/services/longform_media_preflight.py`
- Modify: `scripts/generate_longform.py`
- Test: `tests/test_longform_media_preflight.py`
- Test: `tests/test_generate_longform.py`

**Interfaces:**
- Consumes: `data/longform/{run_id}/media_board.json`
- Produces: downloaded files under `data/longform/{run_id}/media/`
- Produces: `materialize_longform_media_board(data_dir: Path, run_id: str) -> dict`

- [x] **Step 1: Write failing test for materialized media**

Assert that the best Tier A/C/B asset is downloaded to `data/longform/{run_id}/media/` and the selected asset receives `local_path`, `download_bytes`, and `materialized=true`.

- [x] **Step 2: Implement safe materialization**

Use existing media-library download limits and media validation. Do not download files outside the longform run folder.

- [x] **Step 3: Add CLI option**

Add `--materialize-media` to `scripts/generate_longform.py`.

- [x] **Step 4: Run targeted tests**

Run: `pytest tests/test_longform_media_preflight.py tests/test_generate_longform.py -v`

---

### Task 5: Render scenes from media board with motion beats

**Files:**
- Modify: `app/agents/longform_producer.py`
- Test: `tests/test_longform_producer.py`

**Interfaces:**
- Consumes: optional `data/longform/{run_id}/media_board.json`
- Produces: scene videos where still image cards use internal motion beats.

- [ ] **Step 1: Write failing test**

```python
def test_longform_producer_records_media_board_usage(tmp_path, monkeypatch):
    # Create script.json and media_board.json.
    # Monkeypatch narration and ffmpeg helpers to avoid expensive rendering.
    # Assert produce_log.json includes media_board_used=True and media_quality_gate.
```

- [ ] **Step 2: Implement media board loading**

In `run_longform_producer`, load `media_board.json` if it exists and add:

```python
"media_board_used": True,
"media_quality_gate": board.get("gate", {}),
```

to `produce_log.json`.

- [ ] **Step 3: Implement still motion default**

If a scene has only still/image/card media, use internal motion beats:

- Beat 1: establish full frame.
- Beat 2: slow zoom/crop.
- Beat 3: label or focus crop.

Keep the existing card fallback when no asset is usable.

- [ ] **Step 4: Run producer tests**

Run: `pytest tests/test_longform_producer.py -v`

---

### Task 6: Add 30-second preview mode

**Files:**
- Modify: `scripts/generate_longform.py`
- Modify: `app/agents/longform_producer.py`
- Test: `tests/test_generate_longform.py`

**Interfaces:**
- Consumes: `--preview-30s`
- Produces: `data/longform/{run_id}/preview_30s.mp4`

- [ ] **Step 1: Write failing CLI test**

```python
def test_generate_longform_preview_30s_calls_producer_preview(tmp_path, monkeypatch):
    # Monkeypatch preview producer and assert it receives run_id.
```

- [ ] **Step 2: Implement preview output path**

Render only the opening/hook scene or first 30 seconds and write `preview_30s.mp4`.

- [ ] **Step 3: Run CLI tests**

Run: `pytest tests/test_generate_longform.py -v`

---

### Task 7: Server file visibility and documentation

**Files:**
- Modify: `app/services/server_files.py`
- Modify: `agents/06_longform-producer.md`
- Test: `tests/test_server_files_api.py`

**Interfaces:**
- Makes `media_board.json`, `media_contact_sheet.png`, and `preview_30s.mp4` visible under the existing `longform` server-file category.

- [ ] **Step 1: Write or update server-file test**

Assert the longform category can list:

- `media_board.json`
- `media_contact_sheet.png`
- `preview_30s.mp4`

- [ ] **Step 2: Update docs**

Update the longform producer instruction with:

- Asset-first requirement.
- Media board requirement.
- Still-image motion requirement.
- Preview-before-full-render requirement.

- [ ] **Step 3: Run focused backend tests**

Run:

```powershell
pytest tests/test_longform_media_board.py tests/test_longform_media_preflight.py tests/test_longform_producer.py tests/test_generate_longform.py tests/test_server_files_api.py -v
```

---

### Task 8: Deploy and commit

**Files:**
- Commit all changed source, tests, and docs.
- Do not commit `.env`, credentials, generated media, or `.pytest_tmp/`.

- [ ] **Step 1: Run final focused tests**

Run the focused backend test set from Task 6.

- [ ] **Step 2: Check git status**

Run: `git status --short`

Expected: only intentional source/docs/test changes plus ignored local temp if present.

- [ ] **Step 3: Commit with Korean message**

Run:

```powershell
git add app agents scripts tests docs
git commit -m "개선: 롱폼 고품질 미디어 우선 제작 구조 추가"
```

- [ ] **Step 4: Push**

Run: `git push`

- [ ] **Step 5: Deploy backend**

Copy changed backend files to `/home/ubuntu/shorts-factory-be`, restart `shorts-dashboard.service`, and check `/api/health`.

- [ ] **Step 6: Report paths**

Report:

- Local spec path.
- Local plan path.
- New CLI usage.
- Whether deployment succeeded.
