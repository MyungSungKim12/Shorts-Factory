# Longform Pipeline Design

## Goal

Add a separate 5~10 minute longform production path without changing the existing four-times-daily Shorts automation.

## Scope

- Keep the Shorts pipeline and upload schedule unchanged.
- Create longform artifacts under `data/longform/{run_id}/`.
- Produce longform scripts and a previewable MP4 file, not automatic YouTube upload in the first version.
- Reuse the existing Google TTS, media search, FFmpeg rendering, credit guard, and permanent AI opening library where practical.
- Store AI-generated scenes permanently so later Shorts can reuse them when the same verified subject appears.

## Recommended first version

The first production-safe version is a longform file generator:

1. Build or accept a verified story topic.
2. Expand it into a 5~7 minute documentary-style script.
3. Render a 16:9 MP4 with chapter-like narration, subtitles, title card, and source-aware visuals.
4. Use permanent AI assets only for verified exact subjects. If a matching AI asset exists, reuse it. If credit mode allows a new Veo call and a verified reference image exists, create and store a new asset. If not, use stock/reference media.
5. Write `topic.json`, `script.json`, `produce_log.json`, and `output.mp4`.

## Data layout

```text
data/
  longform/
    {run_id}/
      topic.json
      script.json
      produce_log.json
      output.mp4
  media/
    ai_openings/
      {asset_id}/
        reference.*
        master.mp4
        opening.mp4
        metadata.json
```

## Duration policy

- Target: 5~7 minutes for the first version.
- Minimum: 4 minutes.
- Maximum: 10 minutes.
- Narration timing is measured after TTS generation, so video duration follows actual audio rather than model-estimated duration.

## AI asset reuse

- Exact subject matching uses `normalize_subject_key`.
- A ready asset for the same subject is reused before any paid call.
- New paid Veo calls are blocked when the credit guard enters free mode.
- Reused assets remain usable after credits expire.
- AI assets are not part of 7-day work/rejected cleanup.

## Safety

- No automatic longform upload in the first version.
- Existing Shorts `data/work/{run_id}` contract is not changed.
- Secret files and credentials are not copied into artifacts.
- Every used media source is recorded in `produce_log.json`.

