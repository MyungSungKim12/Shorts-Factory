# Story CTA Outro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append a topic-aware Neural2 subscribe-and-like outro to every newly produced story Short.

**Architecture:** Normalize `script.cta` in a focused pure function, then let the story producer synthesize it as a dedicated final audio/visual scene. Reuse the last selected visual, time captions from measured CTA audio, and keep the ranking renderer untouched.

**Tech Stack:** Python 3.12, pytest, Google Cloud Text-to-Speech, ffmpeg, SRT/libass.

## Global Constraints

- Apply only to `CONTENT_FORMAT=story` productions.
- Final video including CTA must be 60–75 seconds and 1080×1920 H.264/AAC.
- CTA must contain both `구독` and `좋아요`; fallback text is `이런 이야기가 더 궁금하다면, 구독과 좋아요 부탁드립니다.`
- Use the same TTS voice and subtitle styling as the story body.
- Preserve ranking behavior and existing story-v1/story-v2 samples.

---

### Task 1: Normalize CTA copy and reserve script duration

**Files:**
- Modify: `app/agents/story_producer.py`
- Modify: `app/agents/writer.py`
- Modify: `app/models.py`
- Test: `tests/test_story_producer.py`
- Test: `tests/test_story_prompts.py`
- Test: `tests/test_story_contracts.py`

**Interfaces:**
- Produces: `normalize_story_cta(value: str | None) -> tuple[str, bool]` where the boolean reports fallback use.
- Produces: validated story body duration of 57–62 seconds from the writer prompt.

- [ ] **Step 1: Write failing normalization and prompt/contract tests**

```python
def test_story_cta_keeps_topic_aware_copy_with_both_actions():
    value, fallback = normalize_story_cta(
        "이런 자연의 비밀이 더 궁금하다면 구독과 좋아요 부탁드립니다."
    )
    assert value.startswith("이런 자연의 비밀")
    assert fallback is False

def test_story_cta_falls_back_when_an_action_is_missing():
    value, fallback = normalize_story_cta("다음 이야기도 구독해 주세요.")
    assert value == "이런 이야기가 더 궁금하다면, 구독과 좋아요 부탁드립니다."
    assert fallback is True
```

Assert the writer prompt contains `duration_sec 합계는 반드시 57~62초` and a non-empty CTA example containing both required words. Update the story contract fixture to accept 57 seconds while retaining the final-output 60-second validation in `media_probe`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_story_producer.py tests/test_story_prompts.py tests/test_story_contracts.py -q`

Expected: FAIL because `normalize_story_cta` does not exist and the prompt/contract still require 60–65 seconds.

- [ ] **Step 3: Implement the minimal normalization and duration changes**

Add the pure normalizer, change only the story writer prompt to 57–62 seconds, require the prompt CTA to contain `구독` and `좋아요`, and lower only the `StoryScript.total_duration_sec`/scene-sum minimum to 57 seconds.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_story_producer.py tests/test_story_prompts.py tests/test_story_contracts.py -q`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add app\agents\story_producer.py app\agents\writer.py app\models.py tests\test_story_producer.py tests\test_story_prompts.py tests\test_story_contracts.py
git commit -m "feat: normalize story CTA copy"
```

### Task 2: Render a timed CTA outro

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `normalize_story_cta(value) -> (text, used_fallback)`.
- Produces: `build_cta_timing(body_duration: float, audio_duration: float) -> dict` with `start`, `end`, and `total_duration`.
- Extends: `produce_log.json` with `cta.text`, `cta.audio_duration`, `cta.tts`, and `cta.fallback_used`.

- [ ] **Step 1: Write failing timing, duration-limit, and logging tests**

```python
def test_cta_timing_uses_measured_audio_after_body():
    timing = build_cta_timing(68.5, 3.2)
    assert timing == {"start": 68.5, "end": 71.7, "total_duration": 71.7}

def test_cta_timing_rejects_final_video_over_75_seconds():
    with pytest.raises(RuntimeError, match="75초 초과"):
        build_cta_timing(73.0, 3.0)
```

Add a producer-flow test with fake TTS/media/ffmpeg boundaries asserting a CTA narration file is synthesized, a final CTA scene is appended, and the log records the normalized CTA and measured duration.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: FAIL because CTA timing/rendering does not exist.

- [ ] **Step 3: Implement the dedicated CTA scene**

Synthesize normalized CTA after body narration, measure its MP3 duration, reject totals over 75 seconds, reuse the final selected media file, add a dark overlay to the visual filter, attach CTA audio, append the scene before final concat, and write a final SRT cue spanning exactly the CTA audio interval.

- [ ] **Step 4: Add CTA production metadata**

Write this shape to `produce_log.json`:

```json
{
  "cta": {
    "text": "주제 맞춤형 CTA",
    "audio_duration": 3.2,
    "fallback_used": false,
    "tts": {"provider": "google", "voice": "ko-KR-Neural2-C", "speaking_rate": 1.05}
  }
}
```

- [ ] **Step 5: Run focused and full tests**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: PASS.

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add app\agents\story_producer.py tests\test_story_producer.py
git commit -m "feat: append timed story CTA outro"
```

### Task 3: Produce and validate story-v3

**Files:**
- Create ignored artifacts: `data/samples/story-v3/*`

**Interfaces:**
- Consumes: existing `story-v2/topic.json` and `story-v2/script.json`, with the script CTA normalized by production code.
- Produces: `output.mp4`, `produce_log.json`, `validation.json`, and `contact-sheet.jpg` under `story-v3`.

- [ ] **Step 1: Copy only topic/script inputs into story-v3**

```powershell
New-Item -ItemType Directory -Force data\samples\story-v3
Copy-Item data\samples\story-v2\topic.json data\samples\story-v3\topic.json
Copy-Item data\samples\story-v2\script.json data\samples\story-v3\script.json
```

- [ ] **Step 2: Render with production Neural2 settings**

Run the story producer with `TTS_PROVIDER=google`, `TTS_VOICE=ko-KR-Neural2-C`, and `TTS_SPEAKING_RATE=1.05`, loading secrets only from the ignored local `.env`.

Expected: `data/samples/story-v3/output.mp4` exists and the log contains a CTA section.

- [ ] **Step 3: Validate and visually inspect**

Run `probe_video`/`validate_sample`, generate a 4×3 ffmpeg contact sheet, and confirm failures are empty, duration is 60–75 seconds, audio is AAC, video is H.264, resolution is 1080×1920, and black ratio is zero.

- [ ] **Step 4: Final verification**

Run: `python -m pytest -q`

Run: `git diff --check`

Expected: all tests PASS and no whitespace errors.

