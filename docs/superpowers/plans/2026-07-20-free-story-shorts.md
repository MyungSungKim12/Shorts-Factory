# Free Story Shorts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the current ranking pipeline while adding a free, high-retention single-topic story format that produces an upload-disabled 1080x1920 sample with Google Neural2 Korean narration.

**Architecture:** Keep the ranking models, prompts, renderer, scheduler, and uploader intact. Route by `CONTENT_FORMAT`, add separate story contracts/prompts/rendering, and put TTS and media selection behind focused service interfaces. The sample command writes only under `data/samples/` and never imports or invokes the uploader.

**Tech Stack:** Python 3.11+, Pydantic 2, pytest, requests, google-auth ADC, gTTS fallback, Pexels/Pixabay APIs, Pillow, ffmpeg/ffprobe.

## Global Constraints

- Existing ranking behavior remains the default when `CONTENT_FORMAT` is absent.
- Story output targets 60–75 seconds, 7–10 beats, and 2–4 second visual shots.
- Use only free Pexels/Pixabay media and locally licensed BGM.
- Record `verification_method`, factual sources, media provider/ID/URL, fallback status, and actual TTS provider.
- Local Google TTS authentication uses ADC; do not create or commit service-account JSON keys.
- `TTS_PROVIDER=gtts` and `CONTENT_FORMAT=ranking` must restore the current production behavior.
- Sample generation must never invoke `run_uploader`, modify `data/work/`, modify SQLite, or change cron.
- Do not commit `.env`, `credentials/`, `.analysis_reference/`, `.tmp_yt_dlp/`, or generated sample media.

---

## File Structure

- `app/models.py`: retain ranking contracts and add discriminated story topic/script contracts.
- `app/content_format.py`: normalize and validate `CONTENT_FORMAT` in one place.
- `app/agents/researcher.py`: retain ranking prompt and add verified single-topic story research prompt.
- `app/agents/writer.py`: retain ranking prompt and add 7–10 beat story prompt.
- `app/services/tts.py`: Google Neural2 synthesis through ADC with explicit gTTS fallback and provider metadata.
- `app/services/media_library.py`: collect, rank, download, and de-duplicate free media candidates with provenance.
- `app/agents/story_producer.py`: render story shots, motion, captions, narration, and optional licensed BGM.
- `app/agents/producer.py`: route story requests to the story producer; leave ranking implementation intact.
- `app/agents/orchestrator.py`: validate/reuse artifacts using the selected format while keeping uploader flow unchanged.
- `app/services/media_probe.py`: inspect dimensions, duration, codecs, audio presence, and black-frame ratio.
- `scripts/generate_sample.py`: research/write/render/verify one isolated sample without uploader imports.
- `tests/test_story_contracts.py`: story schema and routing regression tests.
- `tests/test_story_prompts.py`: story prompt and format dispatch tests.
- `tests/test_tts.py`: provider selection, ADC call, and fallback tests without real network.
- `tests/test_media_library.py`: candidate ranking, provenance, duplicate exclusion, and fallback tests.
- `tests/test_story_producer.py`: shot planning and story render command tests with subprocesses mocked.
- `tests/test_generate_sample.py`: isolated directory and no-uploader safety tests.
- `tests/test_media_probe.py`: ffprobe/black-frame parsing and acceptance tests.
- `requirements.txt`: declare direct `google-auth` dependency.
- `.env.example`: document non-secret story/TTS settings if this tracked file exists; otherwise add the settings to `README.md`.

---

### Task 1: Format Selection and Story Contracts

**Files:**
- Create: `app/content_format.py`
- Modify: `app/models.py`
- Create: `tests/test_story_contracts.py`

**Interfaces:**
- Produces: `get_content_format(value: str | None = None) -> Literal["ranking", "story"]`.
- Produces: `validate_topic(data: dict, content_format: str | None = None) -> dict` and `validate_script(data: dict, content_format: str | None = None) -> dict`.
- Compatibility: documents without `format` validate as ranking documents.

- [ ] **Step 1: Write failing contract and routing tests**

```python
from app.content_format import get_content_format
from app.models import validate_script, validate_topic


def story_topic():
    return {
        "format": "story",
        "topic": "사막 한가운데 호수가 마르지 않는 이유",
        "category": "place_nature",
        "hook_angle": "비가 거의 없는데 물은 남아 있다",
        "target_keyword": "desert lake",
        "core_question": "물은 어디에서 오는가",
        "facts": [{
            "claim": "지하수 공급",
            "value": "지하 대수층에서 물이 공급된다",
            "source": "공공 지질기관",
            "source_url": "https://example.com/geology",
        }],
        "visual_plan": [{"beat": "hook", "keywords": ["desert lake aerial", "dry lake shore"]}],
        "verification_method": "grounded_search",
        "verified_at": "2026-07-20T12:00:00+09:00",
    }


def story_script():
    scenes = [{
        "n": n,
        "role": "hook" if n == 1 else "explanation",
        "narration": f"검증된 내용을 설명하는 {n}번째 문장입니다.",
        "visuals": ["desert lake aerial", "desert water closeup"],
        "duration_sec": 8,
        "emphasis": ["호수"],
    } for n in range(1, 9)]
    return {
        "format": "story", "title": "사막의 호수는 왜 마르지 않을까",
        "description": "검증된 장소 이야기", "tags": ["사막", "호수"],
        "hook": "비가 없는데 호수가 마르지 않습니다.", "scenes": scenes,
        "cta": "", "total_duration_sec": 64,
    }


def test_default_format_preserves_ranking(monkeypatch):
    monkeypatch.delenv("CONTENT_FORMAT", raising=False)
    assert get_content_format() == "ranking"


def test_story_contracts_accept_complete_documents():
    assert validate_topic(story_topic())["format"] == "story"
    assert validate_script(story_script())["total_duration_sec"] == 64


def test_story_rejects_missing_source_url():
    data = story_topic()
    data["facts"][0]["source_url"] = ""
    import pytest
    with pytest.raises(ValueError):
        validate_topic(data)


def test_story_rejects_wrong_duration_or_scene_count():
    data = story_script()
    data["scenes"] = data["scenes"][:6]
    import pytest
    with pytest.raises(ValueError):
        validate_script(data)
```

- [ ] **Step 2: Run tests and confirm the new imports/contracts fail**

Run: `python -m pytest tests/test_story_contracts.py -q`

Expected: FAIL because `app.content_format` and story contracts do not exist.

- [ ] **Step 3: Implement the format resolver and story Pydantic models**

```python
# app/content_format.py
import os
from typing import Literal

ContentFormat = Literal["ranking", "story"]


def get_content_format(value: str | None = None) -> ContentFormat:
    selected = (value or os.getenv("CONTENT_FORMAT", "ranking")).strip().lower()
    if selected not in {"ranking", "story"}:
        raise ValueError(f"지원하지 않는 CONTENT_FORMAT: {selected}")
    return selected
```

In `app/models.py`, add `StoryFact`, `StoryVisualPlan`, `StoryTopicContract`, `StoryScene`, and `StoryScriptContract`. Require non-placeholder facts and HTTP(S) source URLs, 7–10 scenes, at least two visual keywords per scene, roles from `hook/context/problem/mechanism/payoff/close`, sequential scene numbers, and a summed duration from 60 through 75 seconds. Change both validators to choose story when `data.get("format") == "story"` or the explicit `content_format` is story; otherwise call the unchanged ranking contracts.

```python
def validate_topic(data: dict, content_format: str | None = None) -> dict:
    selected = content_format or data.get("format") or "ranking"
    model = StoryTopicContract if selected == "story" else TopicContract
    return model.model_validate(data).model_dump()


def validate_script(data: dict, content_format: str | None = None) -> dict:
    selected = content_format or data.get("format") or "ranking"
    model = StoryScriptContract if selected == "story" else ScriptContract
    return model.model_validate(data).model_dump()
```

- [ ] **Step 4: Run new and existing contract tests**

Run: `python -m pytest tests/test_story_contracts.py tests/test_contracts.py -q`

Expected: PASS, including legacy ranking documents without a `format` field.

- [ ] **Step 5: Commit the contract boundary**

```powershell
git add app/content_format.py app/models.py tests/test_story_contracts.py
git commit -m "feat: add story content contracts"
```

---

### Task 2: Story Research and Writing Prompts

**Files:**
- Modify: `app/agents/researcher.py`
- Modify: `app/agents/writer.py`
- Modify: `app/agents/orchestrator.py`
- Create: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: `get_content_format()` and format-aware validators from Task 1.
- Produces: `_story_researcher_prompt(context: dict, grounded: bool) -> str`.
- Produces: `_story_writer_prompt(topic: dict) -> str`.
- Produces: `run_researcher(..., content_format: str | None = None)` and `run_writer(..., content_format: str | None = None)` without breaking current callers.

- [ ] **Step 1: Write failing prompt/dispatch tests**

```python
from app.agents.researcher import _story_researcher_prompt
from app.agents.writer import _story_writer_prompt


def test_research_prompt_requires_sources_and_visual_plan():
    prompt = _story_researcher_prompt({"recent_topics": []}, grounded=True)
    assert "source_url" in prompt
    assert "verification_method" in prompt
    assert "visual_plan" in prompt
    assert "실재 장소·자연현상" in prompt


def test_writer_prompt_contains_retention_beats():
    topic = {
        "topic": "사막 호수", "hook_angle": "비가 없는데 마르지 않는다",
        "core_question": "물은 어디서 오는가",
        "facts": [{"claim": "지하수", "value": "대수층", "source": "기관", "source_url": "https://example.com"}],
    }
    prompt = _story_writer_prompt(topic)
    assert "60~75초" in prompt
    assert "7~10개" in prompt
    assert "12~15초" in prompt
    assert '"visuals"' in prompt
```

- [ ] **Step 2: Run the prompt tests and verify failure**

Run: `python -m pytest tests/test_story_prompts.py -q`

Expected: FAIL because both story prompt functions are absent.

- [ ] **Step 3: Add story prompts and route the agents**

The research prompt must choose only one stock-footage-friendly subject, prohibit news/current rankings/model-memory for changing claims, require at least two authoritative source URLs, require `verification_method`, and return exactly the story topic schema. The writing prompt must specify: result-first hook in 3 seconds, one small answer by 10 seconds, attention resets near 12–15/25–30/45–50 seconds, 7–10 beats, 60–75 seconds, 2–3 English stock keywords per scene, and no greeting/logo narration.

```python
def run_writer(data_dir: Path, date_str: str, content_format: str | None = None) -> dict:
    selected = get_content_format(content_format)
    # existing topic loading remains unchanged
    prompt = _story_writer_prompt(topic) if selected == "story" else _writer_prompt(topic)
    script_text = call_agent(prompt=prompt, agent_name="script-writer", max_tokens=16000, prefer="groq")
    script_dict = validate_script(extract_json(script_text), selected)
    # existing UTF-8 write remains unchanged
```

Apply the same dispatch pattern in `run_researcher`. In `run_pipeline`, resolve the format once, pass it to the researcher/writer validators, and include `content_format` in the run log. Do not alter uploader invocation or slot limits.

- [ ] **Step 4: Run prompt, contract, cache, and slot tests**

Run: `python -m pytest tests/test_story_prompts.py tests/test_story_contracts.py tests/test_contracts.py tests/test_cache_and_slots.py -q`

Expected: PASS with current ranking category behavior unchanged.

- [ ] **Step 5: Commit story generation routing**

```powershell
git add app/agents/researcher.py app/agents/writer.py app/agents/orchestrator.py tests/test_story_prompts.py
git commit -m "feat: add story research and writing flow"
```

---

### Task 3: ADC Google Neural2 TTS Adapter

**Files:**
- Create: `app/services/tts.py`
- Modify: `requirements.txt`
- Create: `tests/test_tts.py`

**Interfaces:**
- Produces: `TTSResult(path: Path, provider: str, voice: str, speaking_rate: float)`.
- Produces: `synthesize(text: str, output_path: Path, provider: str | None = None) -> TTSResult`.
- Uses: `TTS_PROVIDER=google|gtts`, `TTS_VOICE=ko-KR-Neural2-C`, `TTS_SPEAKING_RATE=1.05`, `TTS_PITCH=-0.5`.

- [ ] **Step 1: Write failing provider and fallback tests**

```python
from pathlib import Path
from app.services import tts


def test_google_provider_uses_configured_voice(tmp_path, monkeypatch):
    seen = {}
    def fake_google(text, output, voice, rate, pitch):
        seen.update(text=text, voice=voice, rate=rate, pitch=pitch)
        output.write_bytes(b"mp3")
    monkeypatch.setattr(tts, "_synthesize_google", fake_google)
    result = tts.synthesize("안녕하세요.", tmp_path / "voice.mp3", provider="google")
    assert result.provider == "google"
    assert seen == {"text": "안녕하세요.", "voice": "ko-KR-Neural2-C", "rate": 1.05, "pitch": -0.5}


def test_google_failure_falls_back_to_gtts(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_synthesize_google", lambda *args: (_ for _ in ()).throw(RuntimeError("ADC unavailable")))
    monkeypatch.setattr(tts, "_synthesize_gtts", lambda text, output: output.write_bytes(b"fallback"))
    result = tts.synthesize("문장입니다.", tmp_path / "voice.mp3", provider="google")
    assert result.provider == "gtts"
    assert result.path.read_bytes() == b"fallback"
```

- [ ] **Step 2: Run the TTS tests and verify failure**

Run: `python -m pytest tests/test_tts.py -q`

Expected: FAIL because `app.services.tts` does not exist.

- [ ] **Step 3: Implement ADC REST synthesis and explicit fallback**

```python
import base64
import os
from dataclasses import dataclass
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession
from gtts import gTTS


@dataclass(frozen=True)
class TTSResult:
    path: Path
    provider: str
    voice: str
    speaking_rate: float


def _synthesize_google(text: str, output: Path, voice: str, rate: float, pitch: float) -> None:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    response = AuthorizedSession(credentials).post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        json={
            "input": {"text": text},
            "voice": {"languageCode": "ko-KR", "name": voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate, "pitch": pitch},
        },
        timeout=30,
    )
    response.raise_for_status()
    output.write_bytes(base64.b64decode(response.json()["audioContent"]))


def synthesize(text: str, output_path: Path, provider: str | None = None) -> TTSResult:
    selected = (provider or os.getenv("TTS_PROVIDER", "gtts")).lower()
    voice = os.getenv("TTS_VOICE", "ko-KR-Neural2-C")
    rate = float(os.getenv("TTS_SPEAKING_RATE", "1.05"))
    pitch = float(os.getenv("TTS_PITCH", "-0.5"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if selected == "google":
        try:
            _synthesize_google(text, output_path, voice, rate, pitch)
            return TTSResult(output_path, "google", voice, rate)
        except Exception as exc:
            print(f"  ⚠️ Google TTS 실패, gTTS 폴백: {exc}")
    _synthesize_gtts(text, output_path)
    return TTSResult(output_path, "gtts", "ko", 1.0)
```

Implement `_synthesize_gtts(text, output)` with `gTTS(text=text, lang="ko", slow=False).save(str(output))`. Add direct dependency `google-auth>=2.38,<3`.

- [ ] **Step 4: Run offline TTS tests**

Run: `python -m pytest tests/test_tts.py -q`

Expected: PASS without contacting Google or gTTS.

- [ ] **Step 5: Commit the TTS adapter**

```powershell
git add app/services/tts.py requirements.txt tests/test_tts.py
git commit -m "feat: add ADC Neural2 TTS adapter"
```

---

### Task 4: Free Media Candidate Ranking and De-duplication

**Files:**
- Create: `app/services/media_library.py`
- Create: `tests/test_media_library.py`

**Interfaces:**
- Produces: `MediaCandidate(provider, media_id, source_url, download_url, width, height, media_type, keyword)`.
- Produces: `choose_candidate(candidates: list[MediaCandidate], used_ids: set[str]) -> MediaCandidate | None`.
- Produces: `fetch_story_media(keywords: list[str], output_stem: Path, used_ids: set[str]) -> tuple[Path | None, dict]`.

- [ ] **Step 1: Write failing selection and provenance tests**

```python
from app.services.media_library import MediaCandidate, choose_candidate


def c(media_id, width, height, provider="pexels_video"):
    return MediaCandidate(provider, str(media_id), f"https://source/{media_id}", f"https://download/{media_id}", width, height, "video", "desert lake")


def test_portrait_unique_candidate_wins():
    chosen = choose_candidate([c(1, 1920, 1080), c(2, 1080, 1920), c(3, 720, 1280)], {"pexels_video:2"})
    assert chosen.media_id == "3"


def test_all_duplicates_return_none():
    assert choose_candidate([c(1, 1080, 1920)], {"pexels_video:1"}) is None
```

- [ ] **Step 2: Run the media tests and verify failure**

Run: `python -m pytest tests/test_media_library.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement candidate collection, scoring, download, and metadata**

```python
@dataclass(frozen=True)
class MediaCandidate:
    provider: str
    media_id: str
    source_url: str
    download_url: str
    width: int
    height: int
    media_type: str
    keyword: str

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.media_id}"


def choose_candidate(candidates, used_ids):
    available = [c for c in candidates if c.unique_id not in used_ids]
    if not available:
        return None
    return max(available, key=lambda c: (c.height > c.width, min(c.width, c.height), c.media_type == "video"))
```

Query up to 8 Pexels videos per keyword, then up to 8 Pixabay videos, then up to 8 Pexels photos. Download only the selected candidate, add its `unique_id` to `used_ids`, and return metadata containing `provider`, `media_id`, `source_url`, `keyword`, `fallback`, `width`, and `height`. If all keywords fail, return `(None, {"provider": "black_bg", "fallback": True, ...})`; never broaden a failed place name to its first word.

- [ ] **Step 4: Run media service tests**

Run: `python -m pytest tests/test_media_library.py -q`

Expected: PASS using mocked HTTP responses only.

- [ ] **Step 5: Commit media selection**

```powershell
git add app/services/media_library.py tests/test_media_library.py
git commit -m "feat: add deduplicated free media selection"
```

---

### Task 5: Story Renderer and Producer Routing

**Files:**
- Create: `app/agents/story_producer.py`
- Modify: `app/agents/producer.py`
- Create: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `synthesize()` from Task 3 and `fetch_story_media()` from Task 4.
- Produces: `build_shot_plan(script: dict) -> list[dict]` with 2–4 second shots and unique shot numbers.
- Produces: `run_story_producer(data_dir: Path, run_id: str, ffmpeg_path: str, work_root: str = "work") -> dict`.
- Produces: `run_producer(..., content_format: str | None = None, work_root: str = "work")` routing without changing ranking defaults.

- [ ] **Step 1: Write failing shot-plan and provider-log tests**

```python
from app.agents.story_producer import build_shot_plan


def test_each_story_beat_becomes_short_visual_shots():
    script = {"scenes": [{
        "n": 1, "role": "hook", "narration": "비가 없는데 물이 남아 있습니다.",
        "visuals": ["desert lake aerial", "cracked desert ground"],
        "duration_sec": 8, "emphasis": ["물이 남아 있습니다"],
    }]}
    shots = build_shot_plan(script)
    assert len(shots) >= 2
    assert all(2 <= shot["duration_sec"] <= 4 for shot in shots)
    assert {shot["keyword"] for shot in shots} == {"desert lake aerial", "cracked desert ground"}
```

- [ ] **Step 2: Run the story producer tests and verify failure**

Run: `python -m pytest tests/test_story_producer.py -q`

Expected: FAIL because the story producer does not exist.

- [ ] **Step 3: Implement story rendering without ranking overlays**

`build_shot_plan` divides each beat duration evenly across its visual keywords, repeating keywords only after every unique keyword has been used and clamping each shot to 2–4 seconds. `run_story_producer` synthesizes one narration file per beat, acquires media per shot with a video-wide `used_ids` set, and encodes 1080x1920 H.264/AAC clips. Apply subtle `zoompan` to still images, full-frame crop to video, and 0.15-second video fade transitions only when they do not desynchronize narration.

Use the existing caption splitting behavior but render 1–2 lines in the lower safe area. The first shot gets only the hook caption; story mode must not call `_build_scene_overlay`, `_opening_filter`, or show a channel intro. Use TTS-native speed and pass audio through without `atempo=1.2`. Mix a locally licensed BGM at `BGM_VOLUME` (default `0.08`) and duck it under narration with `sidechaincompress`; omit BGM cleanly if no licensed local file exists.

Write `produce_log.json` with this minimum structure:

```python
produce_log = {
    "date": run_id,
    "format": "story",
    "output_file": str(output_mp4),
    "planned_duration": script["total_duration_sec"],
    "actual_duration": round(actual_duration, 1),
    "script_sha256": hashlib.sha256(script_file.read_bytes()).hexdigest(),
    "tts": {"provider": tts_provider, "voice": tts_voice, "speaking_rate": speaking_rate},
    "sources": source_metadata,
    "fallback_shots": sum(item["fallback"] for item in source_metadata),
    "experiment": "story_v1_retention",
}
```

In `app/agents/producer.py`, resolve the content format at the top of `run_producer`; return `await run_story_producer(...)` only for story, otherwise execute the existing function body unchanged.

- [ ] **Step 4: Run renderer unit and legacy contract tests**

Run: `python -m pytest tests/test_story_producer.py tests/test_contracts.py -q`

Expected: PASS with ffmpeg and media calls mocked in unit tests.

- [ ] **Step 5: Commit the story renderer**

```powershell
git add app/agents/story_producer.py app/agents/producer.py tests/test_story_producer.py
git commit -m "feat: render high-retention story shorts"
```

---

### Task 6: Sample Isolation and Automated Media Verification

**Files:**
- Create: `app/services/media_probe.py`
- Create: `scripts/generate_sample.py`
- Create: `tests/test_media_probe.py`
- Create: `tests/test_generate_sample.py`

**Interfaces:**
- Produces: `probe_video(path: Path, ffprobe_path: str = "ffprobe") -> dict`.
- Produces: `ffprobe_path_for(ffmpeg_path: str) -> str`.
- Produces: `validate_sample(report: dict) -> list[str]`, returning an empty list on acceptance.
- Produces: `generate_sample(sample_id: str, data_dir: Path, ffmpeg_path: str) -> Path`.

- [ ] **Step 1: Write failing sample safety and probe acceptance tests**

```python
from app.services.media_probe import validate_sample


def test_valid_story_video_is_accepted():
    report = {"width": 1080, "height": 1920, "duration": 66.2,
              "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
              "black_ratio": 0.01}
    assert validate_sample(report) == []


def test_invalid_video_lists_every_failure():
    report = {"width": 720, "height": 1280, "duration": 50,
              "video_codec": "h264", "audio_codec": "", "has_audio": False,
              "black_ratio": 0.4}
    failures = validate_sample(report)
    assert {"resolution", "duration", "audio", "black_frames"} <= set(failures)
```

In `tests/test_generate_sample.py`, monkeypatch researcher, writer, producer, and probe functions; assert all paths contain `data/samples/<sample-id>`, `data/work` remains absent, and the uploader module is never imported.

- [ ] **Step 2: Run the safety/probe tests and verify failure**

Run: `python -m pytest tests/test_media_probe.py tests/test_generate_sample.py -q`

Expected: FAIL because the probe and sample command do not exist.

- [ ] **Step 3: Implement ffprobe validation and the upload-disabled runner**

`probe_video` runs ffprobe JSON output for stream/format data and ffmpeg `blackdetect=d=0.5:pix_th=0.10` for black-frame duration. `validate_sample` requires exactly 1080x1920, 60–75 seconds, H.264, AAC audio, and black-frame ratio at most 0.10.

```python
async def generate_sample(sample_id: str, data_dir: Path, ffmpeg_path: str) -> Path:
    sample_dir = data_dir / "samples" / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    topic = run_researcher(data_dir, sample_id, content_format="story", work_root="samples")
    script = run_writer(data_dir, sample_id, content_format="story", work_root="samples")
    result = await run_story_producer(data_dir, sample_id, ffmpeg_path, work_root="samples")
    output = Path(result["output_file"])
    failures = validate_sample(probe_video(output, ffprobe_path_for(ffmpeg_path)))
    if failures:
        raise RuntimeError(f"샘플 자동 검증 실패: {', '.join(failures)}")
    return output
```

Add compatible `work_root` parameters to researcher/writer, defaulting to `"work"`. The script entry point accepts `--sample-id`, defaults it to the current `YYYYMMDD-HHMMSS`, forces story format and Google TTS only for that process, prints the absolute output path, and contains no import from `app.agents.uploader` or `app.agents.orchestrator`.

- [ ] **Step 4: Run safety and probe tests**

Run: `python -m pytest tests/test_media_probe.py tests/test_generate_sample.py -q`

Expected: PASS and no real network, TTS, ffmpeg, DB, or uploader access.

- [ ] **Step 5: Commit isolated sample generation**

```powershell
git add app/services/media_probe.py scripts/generate_sample.py tests/test_media_probe.py tests/test_generate_sample.py app/agents/researcher.py app/agents/writer.py
git commit -m "feat: add safe story sample generator"
```

---

### Task 7: Full Verification, Configuration Documentation, and Real Sample

**Files:**
- Modify: `.env.example` if tracked, otherwise `README.md`
- Generate but do not commit: `data/samples/<sample-id>/topic.json`
- Generate but do not commit: `data/samples/<sample-id>/script.json`
- Generate but do not commit: `data/samples/<sample-id>/produce_log.json`
- Generate but do not commit: `data/samples/<sample-id>/output.mp4`

**Interfaces:**
- Consumes: the complete sample command from Task 6.
- Produces: a verified local sample and exact rollback/configuration instructions.

- [ ] **Step 1: Document the reversible configuration**

```dotenv
CONTENT_FORMAT=story
TTS_PROVIDER=google
TTS_VOICE=ko-KR-Neural2-C
TTS_SPEAKING_RATE=1.05
TTS_PITCH=-0.5
BGM_VOLUME=0.08
```

Document rollback as `CONTENT_FORMAT=ranking` and `TTS_PROVIDER=gtts`. State that sample generation never uploads and that production schedule remains unchanged until explicit approval.

- [ ] **Step 2: Run the complete offline suite**

Run: `python -m pytest -q`

Expected: all legacy and story tests PASS.

- [ ] **Step 3: Verify the ranking default remains selected**

Run: `python -c "from app.content_format import get_content_format; print(get_content_format('ranking'))"`

Expected: `ranking`.

- [ ] **Step 4: Generate one real story sample with the approved voice**

Run: `$env:CONTENT_FORMAT='story'; $env:TTS_PROVIDER='google'; python scripts\generate_sample.py --sample-id story-v1`

Expected: successful completion and absolute path `D:\ms\shorts-factory-be\data\samples\story-v1\output.mp4`. The command may use configured Gemini/Groq, Pexels/Pixabay, Google ADC, and ffmpeg, but it must not upload.

- [ ] **Step 5: Inspect the real artifacts and provenance**

Run: `python -c "import json,pathlib; p=pathlib.Path('data/samples/story-v1'); print(json.loads((p/'produce_log.json').read_text(encoding='utf-8'))); print((p/'output.mp4').resolve())"`

Expected: `format=story`, `tts.provider=google`, 1080x1920, 60–75 seconds, H.264/AAC, source URLs for all non-black shots, no duplicate provider/media IDs, and the output path.

- [ ] **Step 6: Commit only code and documentation**

```powershell
git add README.md .env.example
git commit -m "docs: describe story shorts configuration"
```

Stage only whichever documentation file actually changed; do not stage generated samples or secret files.

- [ ] **Step 7: Present the sample for human approval**

Provide clickable paths to `output.mp4`, `script.json`, and `produce_log.json`. Report the measured duration, codecs, TTS provider, number of unique media sources, fallback count, and that no upload/cron/database action occurred. Do not deploy or change the schedule until the user approves the sample.

---

## Self-Review Results

- Spec coverage: story direction, retention beats, facts/provenance, free media, de-duplication, Neural2 ADC/gTTS fallback, isolated sample, automated MP4 checks, rollback, and 1-to-2 daily expansion gate are covered.
- Scope boundary: deployment, cron changes, YouTube Analytics scopes, branding, and automatic upload remain outside this plan until sample approval.
- Placeholder scan: no TBD/TODO/“implement later” instructions are present; each task names concrete tests, interfaces, commands, and expected results.
- Type consistency: `content_format`, `work_root`, `TTSResult`, `MediaCandidate`, `build_shot_plan`, `ffprobe_path_for`, `probe_video`, and `validate_sample` names are consistent across producer and sample tasks.
