# News Story motion — proposed extension

Status: Implemented locally; package and desktop verification are tracked in REEL_CREATOR.md.
Branch: `codex/reel-creator-plan`

## Product direction

Extend the existing Reel Creator with coordinated photo, headline, and highlight
animation. Keep a quick workflow: source text/link → Gemini copy → photos → motion
preset → real preview → export/post. A single photo should still produce a useful
reel. Multiple photos should form an ordered story with a stable, readable headline.

The current renderer supports one image, a gentle background push-in, and a Brief
text-panel fade. Its combined image/brand and text layers need to be separated
for the new motion. The application, local render journal, admission checks,
export, music library, and posting handoff can be retained.

## Motion presets

| Preset | Photo movement | Text treatment | Transition |
| --- | --- | --- | --- |
| Breaking Bulletin (default) | Quick push-in, easing to a hold | Sharp horizontal entrances; highlights arrive quickly | 0.22-second crossfade; stronger progress strip |
| Politics Briefing | Gentle, slow pull-back | Slow fades; measured highlight timing | 0.75-second crossfade; no progress strip |
| Photo Story | Wide alternating pans, then push/pull | Soft hook entrance; highlights arrive on photo changes | 0.95-second crossfade; thin progress strip |

Selecting a preset enables story motion and resets per-photo movements, slideshow
effect, and text entrance to Auto with crossfades. Photo framing, crop, timing,
copy, branding and selected audio remain as chosen. After selecting a preset,
explicit movement, text-entrance and transition controls can override it. With
animation disabled, photos and text stay still. Preview and export share these
motion profiles; the static Layout preview does not show their animation.

Motion presets are independent of the existing three visual layouts. The
operator should be able to change either without losing copy or photo order.

## Proposed first release

- One to five photos, with add, replace, remove, and reorder controls.
- Per-photo movement: Auto, Zoom in, Zoom out, Pan left, Pan right, Still.
- Per-photo focal point; full-image and fill-frame treatments remain available.
- Reserve margins around a full-image news card so movement preserves its text.
- Total durations of 10, 15, 20, and 30 seconds; distribute scene timing
  automatically, normally allowing at least three seconds per photo.
- Text entrance: Follow motion preset, Fade in, Slide up/down, From left/right,
  or None. Directional entrances fade and ease into place. Headline appears early and remains
  readable; up to two source-based highlights arrive afterward.
- Smooth easing and roughly 0.3–0.5-second crossfades. No random shaking,
  continuous flashing, or rapid cycling through long blocks of text.
- A brief category/accent entrance, stable source/brand placement, and a subtle
  progress strip. A branded finish and audio fade use the last second.
- Apply music continuously across scene transitions. Do not restart a news sting
  for every photo. Existing custom audio and No music remain available.
- Real motion preview, including pause/scrub, scene changes, and text timing.

The accompanying 10-second concept uses two photos: opening hook at 0–1s;
first photo and key fact at 1–5s; photo change and second fact at 5s; finish and
audio fade at 9–10s. Longer stories distribute more photos across the duration.
The concept uses existing uploaded photos and placeholder copy,
not a claim that those photos depict one verified news event.

## Implementation approach

1. Introduce a versioned scene composition. Preserve existing version-1 drafts
   and immutable render snapshots. Enable the new mode explicitly when editing
   an older draft; new drafts can use the new mode by default.
2. Separate photo/background, readability panel, category, headline, individual
   highlights, brand, and source layers. Preserve the existing Khmer shaping and
   overflow validation. Animate rendered text layers as whole units so Khmer
   character shaping remains intact.
3. Build a deterministic FFmpeg scene timeline with zoom/pan, transitions, and
   timed overlays. Preview and final export use the same timing and geometry.
   Gemini prepares editable hook/highlights and can suggest story beats; it is
   not needed to render animation or to repeat an export.
4. Add a photo strip, per-photo controls, motion presets, and preview scrubbing.
   Extend the native desktop picker for multiple images, reusing the safe cancel
   behavior and authenticated streaming import from the music-picker fix.
5. Keep one renderer, bounded scene count, cancellation/recovery, and the existing
   resource pressure checks. Measure actual five-photo render memory and time;
   use sequential scene intermediates if a combined filter graph exceeds the
   established budget.

## Acceptance checks

- Older drafts and completed videos preserve their previous behavior.
- One-photo and five-photo outputs fully decode at the selected duration.
- Photos remain in operator-selected order through save/reload and retries.
- Full-image movement preserves existing artwork; focal points behave in fill mode.
- Preview/export agree at text entrances and transition boundaries.
- Khmer and English remain readable, with explicit text-overflow errors.
- Audio spans the story with correct fades and no accidental per-scene restart.
- Picker cancellation preserves photos, caption, preset, and pending work.
- Failed imports retain successful selected photos and give a useful error.
- Cancel/retry, duplicate requests, and interrupted exports retain current safety.
- Existing posts, caption edits, account choices, and schedules survive handoff.

The sibling `main-server` repository is absent in this workspace. This proposal
is confined to local composition/rendering and introduces no cloud protocol work.

## Implemented behavior

The initial implementation follows the simple photo-first UI: ordered photo strip,
three motion choices, and optional details. Old drafts require an explicit upgrade.
Text/brand/source use separate canvases; static images are decoded/scaled once
and reused by FFmpeg, substantially reducing preview time. The closing treatment
keeps branding/source fixed with an audio fade; it does not add a separate end card.
The native picker retains successful image imports if another selection fails.
