# Story Framed Layout and Spoken Intro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a spoken-title intro, persistent top title band, bottom subtitle band, and token-safe Korean caption wrapping to every story Short.

**Architecture:** Keep the story renderer isolated from the ranking renderer. Introduce pure layout/timing/text helpers, render all story media into a fixed 1080×1330 center viewport, and apply one final title/subtitle composition over the 1080×1920 canvas.

**Tech Stack:** Python 3.12, pytest, Pillow, ffmpeg/libass, Google Neural2 TTS.

## Global Constraints

- Story format only; ranking output must remain unchanged.
- Canvas is 1080×1920 with 260px top band, 1330px video viewport, and 330px bottom band.
- Never split a Korean token inside a word.
- Title is fixed for the entire video and wraps to at most two lines.
- Intro reads `script.title` before the first hook using the configured TTS.
- Final duration including intro, body, and CTA must be 60–75 seconds.
- Existing YouTube video is not deleted or replaced until the user confirms deletion.

---

### Task 1: Token-safe caption and title wrapping

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `_split_caption(text: str, max_len: int = 22) -> list[str]` that splits only at token boundaries.
- Produces: `_wrap_title(text: str, max_chars: int = 18, max_lines: int = 2) -> list[str]`.

- [ ] Write tests proving `300일 동안` never becomes `30` / `0일 동안`, long unspaced tokens remain whole, and titles use at most two lines.
- [ ] Run `python -m pytest tests/test_story_producer.py -q` and verify the new tests fail for the current character slicing behavior.
- [ ] Replace character slicing with token accumulation and punctuation-aware boundaries; never slice a token.
- [ ] Run the focused tests and verify they pass.
- [ ] Commit with `fix: preserve words in story captions`.

### Task 2: Framed canvas and persistent title

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `STORY_LAYOUT = {"top_band": 260, "video_height": 1330, "bottom_band": 330}`.
- Extends: `visual_filter(..., framed: bool = True)` to create a black 1080×1920 canvas and place media at y=260.
- Produces: `_create_title_overlay(title: str, path: Path) -> dict` returning title line/font metadata.

- [ ] Write failing tests for exact band dimensions, centered viewport placement, bottom-band subtitle style, and at-most-two-line title overlay metadata.
- [ ] Run focused tests and verify RED.
- [ ] Implement the framed filters for images, videos, and preserved-composition Wikimedia images.
- [ ] Generate a transparent 1080×1920 Pillow title overlay using an available Korean font, with dynamic font reduction.
- [ ] Apply title overlay and subtitles in `_finish_video`; place subtitle baseline inside the bottom band.
- [ ] Run focused and full tests.
- [ ] Commit with `feat: frame story videos with fixed title`.

### Task 3: Spoken-title intro and duration budgeting

**Files:**
- Modify: `app/agents/story_producer.py`
- Modify: `app/agents/writer.py`
- Modify: `app/models.py`
- Test: `tests/test_story_producer.py`
- Test: `tests/test_story_prompts.py`
- Test: `tests/test_story_contracts.py`

**Interfaces:**
- Produces: `build_story_timing(intro_audio: float, body: float, cta_audio: float, padding: float = 0.15) -> dict`.
- Extends: `_write_srt(..., intro: dict | None = None, cta: dict | None = None)`.
- Extends: `produce_log.json` with `intro` and `layout` sections.

- [ ] Write failing tests for intro-first timing, 0.15-second padding, intro SRT cue, 75-second rejection, writer target 53–58 seconds, and story contract minimum 53 seconds.
- [ ] Run focused tests and verify RED.
- [ ] Synthesize the title before body audio, measure it, and include it in final timing validation.
- [ ] Reuse the first selected media for a moving intro clip, prepend it before body clips, and add intro subtitles.
- [ ] Shift every body/CTA subtitle timestamp by the intro duration.
- [ ] Record intro TTS, duration, padding, layout bands, and title line count in the production log.
- [ ] Run focused and full tests.
- [ ] Commit with `feat: add spoken title intro to stories`.

### Task 4: Replacement render and production deployment

**Files:**
- Server source: `/home/ubuntu/shorts-factory-be/app`, `tests`, `requirements.txt`
- Server replacement artifact: `/home/ubuntu/shorts-factory-be/data/replacements/20260720-2/output.mp4`

**Interfaces:**
- Consumes: existing production `topic.json` and `script.json` for `20260720-2`.
- Produces: replacement MP4, production log, validation report, and contact sheet without invoking uploader.

- [ ] Run local `python -m pytest -q` and `git diff --check`.
- [ ] Back up production source and current DB before deployment.
- [ ] Deploy source only; preserve `.env`, credentials, data, DB, and cron.
- [ ] Run server `venv/bin/python -m pytest -q` and require success.
- [ ] Copy the existing topic/script to `data/replacements/20260720-2` and invoke only `run_story_producer` with `work_root="replacements"`.
- [ ] Validate 1080×1920, 60–75 seconds, H.264/AAC, audio presence, and zero black-frame failure.
- [ ] Generate and inspect intro/body/CTA frames and provide the replacement file location to the user.
- [ ] Do not call uploader and do not modify the old DB row until the user confirms the old YouTube video is deleted.
