# Reel Creator: product and implementation plan

Date: 2026-10-21 · Revision: 2 · Status: Implemented locally; see verified scope below.
Branch: `codex/reel-creator-plan`

## Recommendation

Add Reel Creator to Studio Center, with an independent composition module and
a supervised local renderer process. Provide one workflow from image to finished
video to the existing posting screen. The renderer owns no browser profile and
can later be reused in a separate application.

The product promise is: **make an existing image look good as a news reel, with
very little editing.** Image-only creation must work. Adding a headline, category,
logo, and music is optional.

| Choice | Decision and reason |
| --- | --- |
| Application | Integrate now: media and Reel publishing already live here. Reconsider a standalone product when it has a separate audience or business. |
| Renderer | Separate module/process so encoding does not block the API or depend on the editor staying mounted. |
| Initial scope | One polished design with two image treatments; grow the gallery after a complete export works. |
| Editing model | Image, layout, text, brand, duration, optional music. Keep the controls focused on this workflow. |
| Delivery order | Prove an attractive actual video, deliver a small complete workflow, then harden and expand. |

Implementation is available in this branch. See [Reel Creator](REEL_CREATOR.md)
for usage, verification, runtime requirements, and remaining platform limits.
No release, cloud contract change, or publishing pilot was performed.

## Changes after reconsideration

The original plan assumed a photograph needing a headline, started with three
templates, and put extensive backend work before the editor. This revision:

- Handles both photographs and already-designed news cards. Existing text and
  branding must not be cropped, covered, or unnecessarily repeated.
- Makes an image-only result useful without requiring the operator to write copy.
- Finishes one design and export workflow before expanding the gallery.
- Treats Khmer line breaking separately from shaping, and explicitly handles old
  previews, repeated render requests, and preservation of existing posting drafts.
- Reuses the backend ownership lock instead of adding another local service.

## Repository findings and limits

- Studio Center is in `manager-app/src/components/CampaignsPanel.tsx`.
- `BatchPostCreator.tsx` stores posting drafts and accepts media filenames.
  A rendered asset must be appended without replacing current work.
- `backend/routers/media.py` uploads/lists shared media. Listing is based on
  extensions; a listed file is not proof of a valid, complete video.
- `backend/config.py` provides persistent `DATA_DIR` and `SHARED_MEDIA_DIR`.
- `backend/main.py` owns startup/shutdown; `backend/runtime_service.py` already
  enforces one backend per data root.
- Resource logic exists in `backend/services/resource_service.py` and automation
  admission. Live also uses automation admission. Review these paths together.
- `backend/idempotency.py` protects selected automation/queue/Live calls, not
  proposed render endpoints. Render deduplication needs explicit implementation.
- Desktop shared media uses `getSharedMediaUrl()` with protected access.
  Private preview endpoints need authenticated fetching; media elements do not
  automatically receive the API fetch authorization header.
- System Python on this host has Pillow 10.2.0 with RAQM and installed Khmer fonts.
  This does not verify the app virtualenv, release runtime, or correct text layout.
  Neither `ffmpeg` nor `ffprobe` was found on the current shell PATH.
- Required sibling `../main-server/docs/PROJECT_CONTEXT.md` and
  `../main-server/IMPLEMENTATION_PLAN.md` are unavailable. Keep this feature local;
  read both projects and the canonical contract before future cloud integration.

## User experience and visual direction

Flow: **Image → layout and optional text → preview → render → review → export
or add to Reel post.**

Add a Reel Creator tab with a large 9:16 preview beside compact settings, stacked
on smaller screens. Explain the image treatments using thumbnail examples.
The operator chooses the treatment; text detection is unnecessary for v1.

### One design, two image treatments

**Newsroom** is the first visual system: charcoal/white, a configurable accent,
clear typography, restrained transitions, and optional brand placement.

1. **Keep full image** — the conservative default for news cards, screenshots,
   collages, and images with embedded text. Fit the complete image over a blurred
   or solid background. Animate the background or frame subtly while the readable
   foreground stays stable. Reserve a separate area for optional new text.
2. **Fill frame** — for photographs. Provide focal-point cropping, a gentle push-in,
   and an optional lower headline over a gradient. Preview the complete motion
   range so the crop stays acceptable throughout the video.

An extremely wide image may remain unreadable at fit size: show a useful warning
and allow adjustment rather than promising automatic readability. If optional
text cannot fit, ask for shorter text or a layout change. Offer a still-motion
setting. Avoid mandatory intro/outro cards, animated embedded text, image warping,
flashes, and scrolling tickers. Safe-area guides are adjustable guidance, not
guaranteed platform boundaries.

Default duration: 10 seconds, with 15/20-second options for more reading time.
Show the important content early and keep it visible for most of the clip.
Validate timing visually; this duration is a design choice, not an engagement claim.

Optional inputs: headline, supporting line, category, source/date, logo or brand
name, accent, and local music. Never invent facts, sources, or timestamps.
A category is not required; “BREAKING” is an explicit choice. Keep the video
headline separate from the posting caption. Save a reusable brand preset without
tying it to a browser profile. Music defaults off; trim it to the selected duration,
fade in/out, and provide a bounded volume control.

Autosave drafts with a revision number and visible save errors. Render a saved
snapshot. Subsequent edits mark an existing preview/export as an older version.
Ignore late preview responses for earlier revisions. Navigation preserves drafts
and render status.

Play the actual MP4 after rendering. “Add to Reel post” appends one media row
through an explicit parent/component handoff, preserving existing rows, captions,
profile selection, and schedule. Guard repeated clicks against duplicate appends.
Export works without scheduling. Rendering never starts publication.

## Composition and rendering

Use versioned JSON containing immutable image/logo/audio asset IDs, template
ID/version, image treatment, focal point/crop, optional text, brand, duration,
and motion preset. Preserve template definitions needed by saved compositions.
Replacing an input creates a new asset ID. Render from an immutable snapshot;
posting captions, accounts, and schedules remain outside the composition.

Use Python for validation/layout and FFmpeg for compositing, animation, audio,
and encoding. Begin with CPU encoding for a predictable baseline; measure before
adding GPU paths. A second browser runtime or video framework is not justified
by this limited design. Keep that decision open if later designs need it.

Rasterize text/graphics into transparent layers once, rather than inserting
user text into FFmpeg expressions. Prefer Pillow with verified RAQM and bundled
licensed fonts. Its language-aware text options depend on RAQM; check the actual
app runtime. See [Pillow text documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html).

Shaping and wrapping are separate requirements. Khmer line breaking needs
language-specific analysis, per [Unicode UAX #14](https://www.unicode.org/reports/tr14/).
Test spaces/no spaces, combining marks, digits, English, and explicit line breaks.
Preserve manual line breaks and offer a tested manual-break fallback initially.
Never split arbitrary code points or assume RAQM proves automatic Khmer wrapping.
Evaluate Pango/ICU if the small engine cannot meet the tested requirements.
Block missing glyphs and overflow with an editing message instead of truncating
or shrinking text below a defined readable minimum. An image already containing
its headline can export without this optional text feature.

Poster, motion preview, and export share layout, fonts, assets, crop, and timing.
Debounce poster requests and cache by composition hash plus renderer version.
Fetch private previews with auth into revocable blob URLs; keep them outside the
shared-media listing. Add an on-demand low-resolution motion preview after the
first export path works. CSS can help cropping interaction, but the rendered
preview is the visual reference.

Output baseline: 1080 × 1920, 30 fps, H.264 MP4, yuv420p, fast-start; AAC when
audio is supplied. Freeze tested tool versions and check required encoders/filters.
Validate streams and duration with FFprobe, then fully decode the short output
to catch corruption that readable metadata can miss. Verify playback in the Tauri
webview and on a target phone; these settings alone do not prove Facebook acceptance.

## Durable local jobs

Persist drafts, revisions, jobs, references, and output identity in a local SQLite
store using standard Python support and existing durability conventions. Use
the exclusive-backend lock and at most one owned renderer process. No broker,
separate daemon, or new cloud assignment protocol is needed for this scope.

States: `queued → rendering → verifying → completed`, plus `failed`, `cancelled`,
and `interrupted`. Persist the snapshot and ownership before execution.
Claim work transactionally. Render creation carries a client request ID atomically
associated with its snapshot/job: timeout retries and double-clicks return that
job; reuse with a different snapshot is rejected. An intentional new render gets
a new request ID. Retrying an interrupted job creates a fresh attempt with retained
diagnostics and one final output identity.

Use FFmpeg's machine-readable `-progress` output rather than parsing human
console text; see [FFmpeg documentation](https://www.ffmpeg.org/ffmpeg.html).
Show verification separately: encoding reaching its end is not completion.
Bound diagnostics and redact session tokens and unnecessary local paths.

Supervise timeout/cancellation and the complete process group. Track a process
identity stronger than a PID, including creation time and ownership token.
Reconcile an owned orphan before retrying after restart; never kill a reused PID
belonging to another process. Interrupted work requires explicit retry.
Editor navigation leaves rendering alone. Desktop closure can stop its backend;
continued rendering requires an independently running backend service.

Stage privately, verify the full output, then atomically promote it under a stable
job-owned MP4 filename on the destination filesystem. Across filesystems, copy
first to an unlisted temporary destination file before rename. Never hand off
partial output. Reconcile promotion-before-database-update crashes by checking
the stable output identity and validity, without duplicate exports.
Retain finished videos until explicit deletion. Clean temporary/preview files
after a documented TTL; retain inputs referenced by drafts or active jobs.

## Resource policy, API, and runtime delivery

Previews and final renders share one budget; final renders take priority over
background previews. New renders wait while publishing/Live work or host pressure
makes admission unsafe. Surface the wait reason. Reuse host metrics/admission
without taking a browser profile lock or consuming a publisher slot.

Bound image dimensions, duration, runtime, encoder/filter threads, and temporary
disk usage. Lower process priority and measure the full process-tree memory budget.
If publication begins later or sustained pressure exceeds that budget, interrupt
the render instead of taking resources from publication; expose a clear retry.
Thread counts are not memory limits. Verify cgroup limits where usable, or a
conservative process-tree memory watchdog where unavailable. One-time admission
does not guarantee isolation. Test native Linux and the Windows/WSL deployment.

Proposed authenticated local `/api/reel-creator` operations: capabilities, draft
save/load, validated asset import, poster/preview, render creation/status/cancel,
and completed-media lookup. Expand only when a UI flow needs it.
Use asset IDs rather than arbitrary paths or remote URLs. Decode inputs, bound
size/pixels/duration, normalize image orientation, and reject malformed content.
Start with JPEG/PNG images, PNG logos, and explicitly validated audio formats.
Use subprocess argument arrays, controlled paths, and timeouts.

Capabilities report missing tools, encoders, fonts, or shaping support with a
repair path. Add tools/fonts to release payloads and runtime install/repair.
Existing Live support does not prove host encoding dependencies are present.

Browser download and desktop export need explicit tested paths. Inspect existing
native-save conventions for video size and Windows/WSL filesystem compatibility;
do not blindly reuse a support-ZIP bytes API. Export copies must avoid overwriting
existing user files. Cancelling an external save retains the app's completed video.

Proposed code boundaries: `components/reel-creator/`, separate frontend
service/types, `backend/routers/reel_creator.py`, and
`backend/services/reel_creator/` modules for layout, templates, assets, jobs,
and process supervision. Store drafts/jobs/staging under `DATA_DIR/reel_creator/`;
only completed MP4s enter `SHARED_MEDIA_DIR`. Use shared Select and Checkbox UI.

## Implementation milestones and exit criteria

### 1. Visual proof and dependency check

Create actual short Newsroom renders for a photograph and a predesigned card,
including Khmer/English. Inspect opening/middle/end, motion, typography, crop,
and optional music. Record tool versions, peak memory, time, and output size on
the test machine. Resolve the missing host encoder and verify packaging strategy.
Derive editor visual references from these actual outputs.

Exit: an attractive, correctly decoded MP4 and a chosen text/layout approach.
Do not wait for all future templates before delivering this proof.

### 2. Usable complete workflow

Implement validated import, one saved draft, Newsroom treatments, poster, one
durable supervised job, progress/cancel, finished playback, and export.
Build the small Studio Center editor alongside the backend. Include request
deduplication, resource bounds, verification, private staging, and basic recovery
from the start; avoid a broad unused API.

Exit: image-only and optional-headline flows work through the desktop app, the
UI stays responsive, and completed media survives backend restart.

### 3. Posting handoff and release readiness

Add safe append-to-posting, brand presets, optional music, motion preview, full
autosave/revision handling, recovery reconciliation, resource-pressure behavior,
and dependency repair. Preserve filename-based media compatibility, authenticated
access, and persistent data across upgrades. Verify native export.
Keep existing posting attempt IDs, fencing, locks, and uncertain-publication
recovery intact.

Exit: the workflow works on explicitly tested runtime paths and posting handoff
preserves existing drafts. Platform acceptance needs a separately authorized
publishing pilot; a successful local render does not establish it.

### 4. Expand after the initial workflow works

Add Editorial (solid text panel for longer headlines) and News Brief (timed
headline/supporting-line reveals) on the same engine. Reuse proven typography,
brand, and motion primitives. Consider batch image-to-reel generation next if
repetitive operator work justifies it.

Defer timelines, multi-scene stories, AI factual writing, voiceover, stock-music
search, AI image animation, cloud rendering, and standalone-app packaging.

## Verification and completion evidence

Focus tests on failure boundaries: corrupt/oversized images, orientation, path
confinement, missing dependencies, glyph/overflow handling, stale previews,
duplicate requests, process cancellation/PID reuse, restart recovery, atomic
promotion, and prevention of incomplete-media handoff. Decode small real renders.

Exercise concurrent automation demand, resource-pressure interruption, backend
shutdown, external-save cancellation, and preservation of existing posting drafts.
Visually review portrait/landscape/square/text-heavy fixtures and transparent logos,
including mixed and no-space Khmer. Compare poster, motion preview, and export;
listen to audio fades/timing. Run applicable backend checks, frontend tests/build,
native playback/export checks, and runtime repair tests.

Record actual tested runtime combinations, sample outputs, and measurements.
Keep unverified paths explicit. Implementation tests and local browser checks are
documented in [Reel Creator](REEL_CREATOR.md); Windows/WSL and packaged-app checks
remain outstanding.

## Future cloud work

Read the missing shared context and canonical worker contract, review impacts on
both projects, and follow the contract-change workflow before cloud work.
Preserve WSS coordination, HTTPS media transfer, authenticated loopback local APIs,
and existing publication recovery semantics. Browser sessions/profile directories
never become composition assets or cloud evidence.

## Source-based Gemini follow-up

The user extended the initial scope with URL/text news copy and bundled audio.
This is implemented inside Reel Creator: bounded public article extraction,
Gemini structured output using existing Settings, reviewed hook/highlights and
post captions, durable editorial drafts and render-caption snapshots, and three
original offline soundtracks. Voiceover, multi-scene editing, and cloud rendering
remain deferred. See REEL_CREATOR.md for current behavior and limitations.
