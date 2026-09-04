# Asset-first Longform Quality Design

## Goal

Raise longform videos from a script-first slideshow-style generator to an asset-first documentary generator. A longform topic should be produced only when the pipeline can secure visuals that clearly match the narration, and every still image should be animated so the video does not feel static.

## Non-goals

- Do not change the four-times-daily Shorts automation.
- Do not auto-upload longform videos yet.
- Do not require every scene to use paid AI video.
- Do not use vague stock footage as proof of a real subject.

## Recommended operating model

Use an asset-first workflow:

1. Pick a candidate topic.
2. Run media preflight before writing the final script.
3. Build a scene-level media board with exact, strong, AI, and fallback assets.
4. Reject or revise the topic if core scenes do not have enough matching visuals.
5. Write narration around the confirmed visuals.
6. Render a short preview first.
7. Render the full longform file only after the preview direction is acceptable.

## Media tiers

### Tier A: exact real media

Use for hooks, evidence, key claims, and chapter openings.

- Existing server/cache media that clearly matches the same subject.
- Wikimedia Commons images with acceptable license and exact subject match.
- NASA public media for space or Earth-observation topics.
- Official/public-domain sources where available.

### Tier B: strong contextual media

Use for explanation, transition, or atmosphere when the subject is not the direct evidence.

- Same location, same phenomenon family, or same object class.
- Metadata must share meaningful subject tokens with the scene query.
- It cannot be presented as direct proof of a named subject.

### Tier C: AI-assisted media

Use for controlled reconstructions, openings, transitions, and abstract scenes.

- Prefer AI generation from a verified reference image.
- Store generated assets permanently with subject key, prompt, source reference, model, and reuse status.
- Reuse existing AI assets before creating new ones.
- If the real-reference gap is high, mark the asset as reconstruction-only.

### Tier D: generic stock media

Use sparingly for bridges only.

- Never use as the main visual for a specific real place, object, or historical claim.
- Avoid repeated sea, dark space, and generic nature loops unless the topic specifically requires them.

## Longform pass gate

A topic can proceed to full longform production only when:

- Every core chapter has at least one Tier A or Tier C visual.
- At least 60% of total planned visual runtime is Tier A, B, or C.
- The first 30 seconds include exact or reference-based visuals, not generic stock.
- No chapter depends only on a single still image without motion treatment.
- The media board records source, provider, license/provenance, match tier, and intended narration use.

When a topic fails the gate, the pipeline should not force the weak topic into production. It should either revise the topic angle toward available assets or return a reviewable failure reason.

## Motion design for still images

Still images must be treated as animated scenes:

- Slow zoom in or zoom out.
- Horizontal or vertical pan.
- Focus crop on the part currently mentioned by narration.
- Soft blurred background layer with the original image in the foreground.
- Highlight box, pointer line, or label for the narrated detail.
- Chapter transitions using subtle film cut, news cut, or map movement.

For any still visual longer than 4 seconds, create internal beats:

- 0~4 seconds: establish the full image.
- 4~8 seconds: crop or zoom into the narrated area.
- 8+ seconds: switch crop, overlay a label, or move to a related asset.

## Audio and narration quality

- Keep narration paced like the stable Shorts voice settings unless a dedicated longform voice is approved.
- Add natural pauses around commas, contrast phrases, and chapter turns.
- Normalize loudness after final mix.
- Match visual beats to narration: when the voice says "the center", "the left edge", or "the lower layer", the frame should move to that area.
- Avoid ambiguous units and loose phrases. Use exact sourced values when available, otherwise explain uncertainty plainly.

## Preview artifacts

Each longform run should be reviewable before full rendering:

```text
data/longform/{run_id}/
  topic.json
  media_board.json
  media_contact_sheet.png
  preview_30s.mp4
  script.json
  output.mp4
  produce_log.json
```

The preview is not a throwaway test. It is the operator approval artifact for visual direction, subtitle style, pacing, and audio feel.

## Reuse policy

- AI clips, AI images, exact reference images, and approved media-board assets are permanent library candidates.
- Longform-generated assets can later be reused in Shorts when the normalized subject key matches.
- Seven-day work cleanup must not delete permanent AI or approved longform library assets.

## Failure handling

- If exact media is unavailable, revise the topic angle before using generic stock.
- If AI generation fails, keep exact real media and mark AI as skipped.
- If the media board is weak, stop before expensive rendering.
- If the narration and visuals do not align, regenerate timing/motion instructions rather than replacing visuals with unrelated stock.

## Success criteria

- A viewer should feel the video was built around real visual evidence, not around filler clips.
- The first 30 seconds should look strong enough to justify a longform click.
- Static images should feel alive through motion, crops, labels, and pacing.
- Every used source should be traceable.
- Shorts automation remains unaffected.
