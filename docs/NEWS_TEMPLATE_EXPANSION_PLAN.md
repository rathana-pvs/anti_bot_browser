# Five additional news templates

Status: Proposed design and implementation plan; no new templates implemented by this document.
Date: 23 October 2026
Scope: English news, silent playback first, 9:16 and 4:5 for every new template.

## Outcome

Add five separate visual and editorial formats to the four existing layouts.
Each must differ in composition, content hierarchy, typography, motion and
storytelling. A palette swap or a different headline entrance does not qualify
as a new template. Existing Newsroom, Editorial, News Brief and Daily Brief
remain available, with their saved drafts and outputs preserved.

The five additions are **Impact Wire**, **Lens Report**, **Event Line**,
**News in Numbers** and **On Record**. Their silhouettes should remain
recognizable in grayscale, without logos or category colors.

The user narrowed the ratio requirement to **9:16 and 4:5**. Both are mandatory
for all five additions. Square, landscape and custom ratios are outside this
extension; existing ratio support in older layouts is not removed.
Voice generation, AI template selection, footage ingestion and publishing changes
are outside this phase. Existing prepared audio can still be used.

## Current implementation and constraints

- The app has three FFmpeg layouts plus the new Daily Brief Remotion adapter.
- `daily_brief.py` prepares rasterized image/text layers, using the same assets
  for a poster and the video. `src/app.tsx` animates these prepared layers.
- The current Remotion manifest carries a fixed base/photo/text triple. It does
  not yet describe quotes, charts, event timelines or independently timed facts.
- The adapter currently requires 9:16 and a source label. New 4:5 support needs
  real layout work; the existing 9:16 raster must not simply be cropped.
- The backend already owns immutable jobs, asset access, admission, cancellation,
  recovery, audio mixing, FFprobe checks, full decode and atomic promotion.
- Keep its 768 MiB process-tree RSS cap and 300-second render-stage timeout.
  The previous 15-second, five-photo Daily Brief fixture peaked near 661 MiB;
  this is a reference measurement, not proof that new templates fit the budget.
- Render one scene at a time and separate browser rendering from video encoding.
  Do not run an independent frontend renderer that bypasses backend admission.
- The sibling `main-server` repository is absent. This plan changes local editing
  and rendering only; no cloud contract or remote state changes are proposed.

## The five designs

### Impact Wire — urgent bulletin

**Use:** confirmed breaking/developing news, public alerts, major announcements.

**Visual identity:** near-black/navy canvas, high-contrast white typography and
red accent blocks. The opening is a large headline composition with a diagonal
edge and a short category strap. A rectangular photo enters as a supporting
visual; a compact fact rail replaces the opening title after it has been read.
No rounded photo card, glass effect, scrolling ticker or constant flashing.

**Story:** headline → what happened → who/where is affected → latest confirmed
status. Use a short headline (about 8–10 words) and 2–3 short fact beats.

**Motion:** a single 8–12-frame directional reveal, decisive cuts, fact blocks
arriving one at a time. Keep each fact still while it is being read. A photo may
move within a safe bounded mask only if its framing allows it. Animate off holds
all elements still. Breaking/developing labels are operator choices.

**9:16:** title-led opening; subsequent photo above a stacked fact rail.
**4:5:** headline/facts in the left column, photo in the right, source along the
bottom. The content order stays the same; compactness does not remove a fact.

**Required inputs:** headline, 2–3 facts, one or more photos, source. Urgency label
is optional and never inferred from the photo.
**Suggested length:** 20–30 seconds; extend holds when the copy needs more time.

### Lens Report — photographic news feature

**Use:** community reporting, environment, culture, human-interest news.

**Visual identity:** photography fills the visual field. Warm neutral text,
editorial serif headlines and a restrained masthead establish a documentary
feel. Minimal rules and no card frame. A readability gradient follows the caption
area; the subject remains visible. One strong image is enough, with up to five
ordered photos for a longer feature.

**Story:** scene-setting image → source-grounded detail → impact on people →
closing context. Captions describe the actual photograph or clearly distinguish
illustrative imagery. This is not a slideshow with the same headline pasted over
all photos: each beat has its own caption.

**Motion:** slow, bounded image movement; caption dissolves; a gentle reveal at
photo boundaries. Reserve the adjacent scene's overlap frames explicitly, render
those handles sequentially, and join with a bounded FFmpeg transition stage.
If a subject/crop cannot tolerate movement, hold that image still.

**9:16:** image fills the canvas; caption occupies a protected lower area.
**4:5:** wider photographic composition with a shallower bottom caption band.
Use independently saved 4:5 framing, not a crop of the vertical export.

**Required inputs:** 1–5 photos, a headline, 1–5 descriptive caption beats, source.
**Suggested length:** 25–45 seconds. No artificial parallax or reconstructed
background is required.

### Event Line — how the story unfolded

**Use:** policy changes, court proceedings, incident developments, negotiations.

**Visual identity:** a light paper-toned canvas, dark readable type, amber
milestones and a strong timeline path. Dates/times and event labels dominate;
photographs are optional small context panels. No large hero photo or alert strap.

**Story:** initial event → subsequent developments → current status. Display
3–5 events with a separate event time/date, title and short description. Preserve
source chronology and uncertainty; distinguish an event date from article
publication date. Relative labels such as “Earlier” need explicit supplied text.

**Motion:** the path draws between milestones; the current event is emphasized,
then settles into the visible context. Earlier events stay legible; no continuous
scrolling. The active milestone and path progress are frame-based values.

**9:16:** a vertical timeline with event text to its right.
**4:5:** a compact stepped timeline over two rows, with explicit sequence numbers
and a clear reading path. Verify ordering with real 3-, 4- and 5-event stories.

**Required inputs:** 3–5 ordered events with supplied date/time labels and source.
A photo is optional; text-only export must be supported explicitly.
**Suggested length:** 30–45 seconds; longer event descriptions need longer holds.

### News in Numbers — explain the figures

**Use:** budgets, economic reports, public-service capacity, election results
when the supplied data supports the comparison.

**Visual identity:** a dark analytical canvas, one dominant number, a restrained
cyan accent and a large directly labeled chart. Numbers and evidence carry the
scene. No photographic hero, quotation ornament or persistent news ticker.

**Story:** main figure → comparison → meaning/limitations → source/date. Begin
with a whole-story headline, then reveal one comparison at a time. Support one
bar comparison or one simple line series per reel in the first implementation.

**Motion:** bars grow or a line draws to the supplied values; labels remain
accurate and readable. Do not animate a displayed number through invented
intermediate values. No decorative financial chart without real input data.

**9:16:** main figure above chart, explanation below.
**4:5:** main figure beside the chart, with the explanatory sentence below both.
Recalculate axes, tick spacing and labels for the new chart area.

**Required inputs:** 2–5 values/points, labels, units, reporting period, source and
an explanatory sentence. Support zero/negative values deliberately. Bars start
at zero; any truncated line-chart scale must be conspicuous. Do not interpolate
or infer missing data. Photographs are optional.
**Suggested length:** 25–40 seconds. Chart source can differ from article source
and must be retained.

### On Record — statement and context

**Use:** public statements, interviews, official announcements and responses.

**Visual identity:** a pale editorial background, oversized quotation marks,
a prominent quotation in serif type and an asymmetrical speaker/attribution
area. A circular portrait or a modest rectangular document excerpt is optional.
A small neutral context strip follows the quotation. No charts, timeline rail,
photo card or breaking-news treatment.

**Story:** identify the statement → display the exact quote → explain its context
→ state the attributed response or latest verified status, when supplied.
A quoted claim is presented as the speaker's claim, not as verified narration.

**Motion:** quotation punctuation and whole text groups reveal in a restrained
sequence; attribution stays visible. Context enters on its own beat. Avoid a
word-by-word typing effect, lip-sync, synthetic presenters or invented speakers.

**9:16:** quote above speaker/portrait, context on the next beat.
**4:5:** portrait/attribution column beside the quotation, with context below.
If no portrait is provided, use a typographic attribution; never fabricate one.

**Required inputs:** exact quote, speaker name/role, attributable source and
context. Keep the original quote/source passage for checking. Translations must
be labeled; review is required for omissions or paraphrases. A paraphrase cannot
be enclosed in quotation marks.
**Suggested length:** 20–35 seconds, increased for longer quotations.

## Shared ratio and reading rules

| Ratio | 1080 export | 720 export | Motion preview |
| --- | --- | --- | --- |
| 9:16 | 1080×1920 | 720×1280 | 360×640 |
| 4:5 | 1080×1350 | 720×900 | 360×450 |

- Introduce named geometry functions per template and ratio, including image,
  headline, body, attribution and optional graphic rectangles.
- Use both width and available height to fit text. Keep readable minimums; reject
  overflow with an editing message instead of cutting text or shrinking forever.
- At 1080 width, target 32–40 px body type, at least 28 px body when fitting,
  and 24–28 px attribution. Specific headline sizes differ by template.
- Use adjustable safe-area guides; start with roughly 6% side margins and a
  protected bottom band. Exact platform overlays still require visual review.
- Keep the same facts and asset IDs across ratio changes. Store each ratio's
  photo framing separately, preserving existing behavior.
- Draft copy can be shared across the two ratios, but line breaks, crop, chart
  geometry and caption placement are recomputed. Preview and export use the
  exact same layout function for the chosen ratio.
- Silent reels should normally allow roughly 2.5–3 words/second. Compute required
  holds from all visible reading copy; use at least 4 seconds for a new fact.
  Explain a timing conflict instead of silently trimming copy or a quotation.
- Total story duration remains bounded at 60 seconds. When narration is supplied,
  it must also fit the measured recording. Rendering never calls a voice service.

## Editing and data model

Use **Composition v3** for the five new templates, with an explicit upgrade from
an existing draft. Keep v1/v2 readers and snapshots unchanged. New stable IDs:
`impact_wire`, `lens_report`, `event_line`, `news_in_numbers`, `on_record`.

Preserve existing shared brand, headline, source display, photos, post caption
and audio fields. Add a versioned `news_story` object containing:

- `schema_version` and `layout_revision` for durable interpretation of a snapshot.
- Stable beat IDs, beat headings/bodies, optional asset IDs, explicit duration or
  Auto, and evidence/source passages when available.
- A discriminated payload: facts, photographic captions, dated events, chart
  data or attributed quotation. Its kind must agree with the selected template.
- Optional structured provenance: source URL, publication timestamp and, when
  different, chart/quote source. Unknown dates remain absent.

Current `validate_assets()` requires a cover image. Change that rule only for
v3 Event Line, News in Numbers and On Record, whose designs allow text/graphics
without a photo. Validate every referenced asset against local authorized asset
records; reject arbitrary filesystem paths and remote media URLs.

Keep a saved payload for each template while exploring another, with explicit
field mapping and validation before rendering. Do not discard chart values,
quotes, caption drafts, source passages or ratio framing during selection.

In the editor, show five distinct thumbnail previews with a short “Best for”
label. On selection, show only the relevant fields: fact beats, photo captions,
events, chart values or quote/speaker. Use the project's shared Select and
Checkbox components. Place source/date and ratio controls where they are easy
to find. Show missing required inputs before rendering, and require review of
any generated claims. Initial selection is manual; Jev can be considered later.

## Rendering implementation

1. Add a registry of template IDs, schema versions, supported ratios, required
   inputs and geometry functions. Expose template capabilities separately from
   base FFmpeg readiness; unsupported choices should explain why they are disabled.
2. Replace the new templates' fixed base/photo/text triple with a small typed
   layer manifest: layer ID, prepared asset or vector geometry, bounding box,
   order and frame-based entrance/hold/exit information. Retain the existing
   Daily Brief adapter for old jobs.
3. Use Pillow/RAQM with bundled fonts for text fitting and text rasterization;
   reuse those text layers for both posters and Remotion. Local SVG geometry can
   describe chart marks, milestone paths and masks. Load no remote fonts/assets
   while rendering.
4. Give every new template its own layout and motion module. Shared primitives
   cover text, fitted photos, attribution and bounded interpolation; do not build
   five configurations of one identical layout.
5. Continue sequential scene rendering, bounded encoding and existing audio mix,
   followed by FFprobe, full decode and atomic promotion. Transition handles count
   toward the timeline correctly and are not added to total duration twice.
6. Persist rendering/layout revisions in the immutable job snapshot. Adding a
   template or rebuilding the optional runtime must not reinterpret completed
   jobs. Missing required runtime/revision produces an explicit retry/setup error.

### Libraries and assets

Keep **Remotion + FFmpeg + Pillow/RAQM** as the core. Begin with frame-based
`interpolate()` and `spring()` already available from Remotion. Add helpers only
where they simplify a specific effect:

| Candidate | Purpose | Decision |
| --- | --- | --- |
| `@remotion/shapes` | Geometric masks, arrows and callouts | Optional for Impact Wire and Event Line |
| `@remotion/paths` | Timeline and chart path reveal geometry | Optional for Event Line and News in Numbers |
| `@remotion/transitions` | Declarative scene transitions | Evaluate only with sequential rendering/overlap handles; not a first-phase requirement |
| `@remotion/layout-utils` | Browser-side text measurement | Deferred; keep Python as the fitting authority for poster/export agreement |
| Source Serif 4 | Distinct editorial headline/quote typography | Bundle selected static font weights and their OFL notice; no render-time font download |

Pin any added `@remotion/*` package to the same tested version as the installed
Remotion packages (currently 4.0.538). Existing core rendering is enough for the
first two templates. Avoid adding a second animation framework, WebGL/3D, remote
chart service or external video-generation API in this extension.

References checked for planning:
[Shapes](https://www.remotion.dev/docs/shapes/),
[Paths](https://www.remotion.dev/docs/paths/),
[Transitions and matching package versions](https://www.remotion.dev/docs/transitions/),
[Layout utilities](https://www.remotion.dev/docs/layout-utils/),
[Source Serif source and OFL license](https://github.com/adobe-fonts/source-serif).

## Delivery sequence

1. **Design proof:** five storyboard concepts in both ratios using clearly marked
   fictional copy. Compare opening, middle and ending frames. Keep source labels
   and review phone-scale reading comfort before implementing animation.
2. **Foundation:** v3 schema, registry, two-ratio geometry, template-specific editor
   state, typed layers, compatible validation and capability reporting. Keep this
   local; do not alter worker assignments, leases or cloud protocols.
3. **First release pair:** Impact Wire, then Lens Report. Produce real silent demo
   MP4s in both ratios; validate readability, framing and measured resource use.
4. **Structured story pair:** Event Line and On Record, including source-aware
   editing, ordering and attribution checks. Produce both-ratio demos.
5. **Data template:** News in Numbers, including units/period/chart-source inputs,
   chart semantics and deterministic vector animation. Produce both-ratio demos.
6. **Integration and delivery:** regression checks, poster/preview/export parity,
   existing posting handoff, capability UX and runtime preparation. Validate source
   Linux first. The optional Node/browser runtime still needs packaged desktop and
   Windows/WSL installation work; do not claim cross-platform readiness in advance.

No elapsed-time estimate is treated as guaranteed. Complete each pair and measure
it before making the next pair available. This document and the accompanying
concept previews are planning artifacts; no new renderer or dependency is installed.

## Acceptance and verification

- All **10 template/ratio combinations** render correctly with realistic short and
  long English copy. Each new layout is recognizable without its accent color.
- Check settled poster/preview/export frames at the opening, middle and finish,
  plus text entrances and transitions. No clipped glyphs or illegible attribution.
- Ratio switching retains content, ordering, asset IDs and independent framing.
  Geometry and output dimensions match the selected ratio exactly.
- Events retain supplied chronological order; quotes match their saved source;
  chart values/units/labels match supplied data. Do not invent missing content.
- Image-optional templates export without manufacturing a cover image. Photo-based
  templates clearly require their photos. Crops/subjects survive any motion.
- Timeline arithmetic is frame-exact, including transition handles. Audio spans
  the full story, and a silent/no-music output contains no unintended sound.
- Verify cancel during browser rendering, scene joining and audio muxing; verify
  retry of saved settings, restart reconciliation and old-draft compatibility.
- Measure peak process-tree RSS and elapsed time for maximum-size assets and the
  60-second/maximum-beat case at both ratios, plus 24/30/60 fps. Keep the existing
  768 MiB/timeout limits; optimize or reject an unsupported combination explicitly.
- Run FFprobe and full decode on actual 720/1080 exports, with and without music.
  Verify H.264/yuv420p, dimensions, duration, selected fps and expected AAC stream.
- Existing drafts, previews, completed exports, captions and posting handoff pass
  regression checks. Rendering remains local and does not publish.

## Lens Report implementation update — 23 October 2026

Lens Report is the first template being delivered from this plan. The scoped
foundation uses Composition v3 with layout revision 1 and per-photo title/caption
fields. It does not yet introduce the event/chart/quote payload registry needed
by the other planned templates. Both requested ratios have independent layouts;
the existing backend lifecycle owns rendering and bounded dissolve joins.
See REEL_CREATOR.md for editing and runtime preparation instructions.
