# Reel Studio modular redesign plan

Date: 2026-10-24
Status: UI/UX planning only; no implementation authorized in this update.
Scope: All Reel Studio tabs and their shared preview/export surfaces.

## Goal and agreed requirements

Replace template-dependent editing with independently editable scene elements.
Keep the everyday workflow small, while allowing features to grow without adding
another complete template or duplicating template-specific controls.

Optimize the default interface for repeated use. Show the task, its controls,
and the result; move explanations and uncommon options to on-demand help or
contextual settings. New users can open help without making experienced users
read it on every visit. This applies to Story, Media, Style, preview, and export.

The Story tab has exactly two AI generation actions:

1. **Generate screen text** — generates scene headlines, supporting text, and
   relevant metadata using the current duration, scene count, enabled elements,
   language, layout space, and requested text amount.
2. **Generate post caption** — generates only the posting caption, explicitly
   requested when the user is ready to post, including after rendering/export.

Repeated screen-text generation must never generate, replace, or clear a post
caption. There are no separate headline, supporting-text, adapt, or shorten AI
buttons in the redesigned Story tab. Users edit individual fields manually.
Review, Apply, Discard, and Read article are ordinary actions, not additional
generation modes. Narration remains a separate optional audio feature.

## Current implementation findings

- `ReelCreator.tsx` coordinates Story, Media, Style, previews, drafts, and render
  jobs. The composition is a largely flat object with template-specific behavior.
- `ReelStyleEditor.tsx` exposes different controls for different templates;
  movement choices can also reset other movement settings.
- `ReelTextEditor.tsx` edits shared text, while photo-specific headlines live in
  Media. This splits ownership of the same content across tabs.
- `NewsAssistant.tsx` calls template-aware generation and applies its post caption
  together with screen-text suggestions for most existing generation intents.
- `template_news.py` extends the shared `NewsCopy` schema, which requires a post
  caption and template/audio suggestions. Hiding the caption field alone would
  leave the unwanted provider work in place.
- `templateContent.ts` uses a combined review key that includes caption edits.
  The two actions need separate request and review scopes.
- `models.py` links composition versions and validation to template identity.
  Rendering is distributed across `composition.py`, `story.py`,
  `flexible_scene.py`, `lens_report.py`, and `daily_brief.py`.
- `store.py` stores editorial data on drafts and snapshots captions on render
  jobs. The final posting handoff must support a caption prepared after rendering.

The sibling `../main-server/docs/PROJECT_CONTEXT.md` and
`../main-server/IMPLEMENTATION_PLAN.md` were unavailable at the instructed paths.
This plan is scoped to the local editor, local APIs, and rendering. It proposes
no worker protocol, cloud job, or publication changes. Read the missing context
and inspect both projects before any later shared-contract work.

## Proposed user flow

**Add media/source → set duration and text amount → generate screen text →
edit scenes and appearance → preview/render → generate post caption when ready
→ add to posting draft.**

Manual text editing and export remain available without AI. A post caption is
optional and never blocks a preview or render.

Retain Story / Media / Style as navigation in the first release to limit the
size of the transition. Use one scene selection across all tabs and the preview.

| Area | Responsibility |
| --- | --- |
| Story | Source, screen-text generation, scene text/metadata, collapsed post-caption section |
| Media | Import, order, crop, framing, photo treatment, music and narration |
| Style | Shared style, scene arrangement, optional elements and their appearance |
| Preview | Selected scene, element selection, visible result and playback |
| Export | Output dimensions/frame rate, render status, export and posting handoff |

Duration is a shared reel setting with one timing editor. Story shows a compact
duration value that opens that editor; it does not duplicate the full timing
form. Export quality settings stay in Export. Use a shared scene strip and
contextual settings without requiring another composition-model rewrite.

## UI/UX rules across the whole studio

- Keep Story / Media / Style as the only editor tabs for this redesign. Preview
  and Export are shared surfaces, not extra navigation steps. Audio lives in Media.
- Use short labels and visible values. Remove introductory paragraphs, promotional
  wording, repeated recommendations, template descriptions, and always-visible
  explanations of what a button does.
- Keep common controls directly accessible. Collapse optional configuration,
  technical details, and evidence, rather than hiding the main editing workflow.
  An inactive optional feature has one compact entry, not a full disabled form.
- Group settings by the selected scene or element. Expand one contextual settings
  group at a time instead of rendering every module's form simultaneously.
- Use stable defaults and remember user-chosen panel expansion, source mode,
  active tab, scene selection, and scroll position when navigating. A new draft
  resets scene-specific state; do not carry selection from a different draft.
  Do not reopen dismissed help on each visit. Avoid separate Beginner/Expert modes.
- Give each setting one editing home. Cross-links can focus that control; do not
  duplicate complete forms across tabs. Shared selection stays synchronized.
- Keep readable type, clear labels, reasonable spacing, and large enough targets.
  Reducing text must not mean tiny controls or unexplained icon-only actions.
- Provide help through a consistent help menu and occasional info controls for
  unfamiliar settings. Tooltips must work on keyboard focus; essential actions
  and error recovery must never depend on hover.
- Keep routine success quiet: one save indicator in the header; small transient
  feedback only where useful. Do not show persistent “ready,” “fits,” or “saved”
  cards, nor announce every background fit check.
- Show problems only where they matter, once, with a clear recovery action.
  Keep errors, missing requirements, stale output, and risk of overwriting edits
  visible; put diagnostics behind Details. Use a small tab/scene badge to route
  users to an issue instead of repeating banners throughout the studio.
- Do not show implementation terms such as composition version, fingerprints,
  provider retry behavior, renderer revision, or storage paths in normal flows.
  Technical details remain available through Help/Details where useful.

### Tab and surface inventory

| Area | Visible during normal use | Open only when needed | Remove from the default surface |
| --- | --- | --- | --- |
| Header | Draft name/switcher, New, quiet save state | Draft management, help | Intro copy and repeated export specifications |
| Story | Source entry or compact loaded source, text amount, duration value, Generate screen text, selected scene text | Source details, language/tone options, metadata, post caption, evidence | Template summary cards, fit-success panels, per-scene word-budget reports, extra AI actions |
| Media | Scene strip, Add media, selected photo controls, compact Music/Narration entries | Crop/framing workspace, per-scene timing, audio settings | Long movement explanations, text editing duplicated from Story, expanded inactive audio forms |
| Style | Arrangement previews, shared font/color controls, compact element list | Selected element appearance/animation, brand settings, preset gallery | Full template catalog with prose, duplicate motion presets, migration instructions in ordinary controls |
| Preview | Large canvas, selected-scene state, Layout/Video switch, playback | Guides, guide adjustment, output details | Welcome slogans, repeated specs, persistent instructional captions |
| Export | Main render action, compact output value; completed video actions when available | Resolution/fps, render history, diagnostics | Always-open technical settings, repeated ready/status cards, routine local-render explanations |

### Story interaction details

- Support manual scene text editing without first using Source or AI. Show the
  source entry when generation needs it; keep manual editing readily available.
- Once a source is loaded, reduce it to its title plus an Edit action. Keep the
  full article, extraction counts, and source passages behind an expansion.
- Keep Text amount and the current duration near Generate screen text. Put
  language and tone in one small Options panel with remembered values.
- Edit one selected scene at a time. Supporting text appears only when its element
  is enabled. Category/source fields appear when their elements are relevant,
  in a compact Metadata group. Do not repeat a shared source editor per scene.
- During suggestion review, use the same scene editing region for pending text,
  with Apply / Discard. Keep the current text safely retained. Offer Compare on
  demand instead of displaying full current/proposed scene lists at all times.
- Post caption starts as a single collapsed row, accessible at any time. Opening
  it reveals Generate post caption and its editor. Remember explicit expansion;
  never force it open just because screen text was generated.
- Caption target appears as a short Draft / selected-video value. Show additional
  context only when the chosen video differs from the current draft.
- Put data-use explanations and supporting passages in on-demand details. Show
  setup errors beside the requested action with a Settings link, not a recurring
  introductory setup panel when everything is configured.

### Media and audio interaction details

- Use the shared scene strip for selection and reorder. Add/replace/remove media
  belongs here; show Crop, Fit/Fill, and movement for the selected photo.
- Open cropping/framing in a focused workspace. Keep fine positioning, per-photo
  overrides, Reset, and Apply to all there instead of expanding the main tab.
- Put total/scene timing in one Timing panel reachable from Story's duration value
  and the selected scene's duration. Show the effective value without a paragraph
  explaining the timing algorithm.
- Use compact Music and Narration rows showing Off or the selected item. Expanding
  Music shows choice, preview, and volume; expanding Narration shows script,
  voice preparation and playback. Voice/rate and mixing controls are secondary.
- Move narration out of Story. Preserve its existing optional script-generation
  capability within its own audio panel; it is not a third Story generation action.
  Keep any source context internally shared without making users paste it twice.
- If narration is enabled but not prepared, show one actionable state beside it
  and link the blocked render action to that location.

### Style interaction details

- Use a few visual arrangement previews and a compact preset picker. Preset names
  and thumbnails replace marketing descriptions and duplicate quick-preset rows.
- Keep font and accent controls at reel scope; individual element overrides appear
  only when that element is selected. Show a small Shared / This scene scope value
  where an edit could otherwise affect an unexpected set of scenes.
- Represent Headline, Supporting text, Progress, Source, and Brand as compact
  element rows. Show optional elements through Add element; selecting a row opens
  its controls. Avoid a long stack of always-open module cards.
- Text content stays in Story. Style owns typography, position, panel background,
  and text entrance. Photo movement stays in Media; scene transitions stay in
  Timing. Remove the competing global animation/motion controls for new drafts.
- Offer text-panel/background visibility in the selected element's settings.
  Subject cutout, if added later, belongs to photo tools in Media.
- Keep migration choices in draft management. Legacy-only options appear only
  on legacy drafts and do not add explanatory text to every new draft.

### Preview, export, and feedback interaction details

- Make the preview visually dominant. Put guides in its toolbar, off by default;
  show guide adjustment only when enabled. Use one compact format/duration value.
- Keep one primary render action and a secondary motion-preview action. Output
  quality opens from a compact setting; keep 1080-wide / 30 fps as the new-draft
  default, while respecting saved values.
- Reuse the render action area for queued/rendering/verification progress and
  Cancel. Do not repeat the same progress in multiple cards or overlays.
- After completion, expose Export MP4 and Add to Reel post beside the selected
  result. Keep previous renders in a collapsed history picker.
- Show “Earlier version” beside stale video with a Render action; do not repeat
  the warning on every tab. Keep the existing video playable.
- Use short issue text with an action, for example “Scene 2: text too long” →
  open that scene. Detailed fit measurements remain available on demand.
- Keep tab switching and save updates stable: preserve focus/selection, avoid
  full-panel loading screens, and reserve busy indicators for the active operation.
- On narrow windows, stack editor and preview with reachable actions and no
  horizontal overflow; do not create overlapping sticky bars or nested scroll traps.

### Story tab

```text
Source
  Article URL / pasted text
  Loaded source: title · Edit

Screen text
  Duration: current reel duration
  Text amount: Less / Standard / More
  [Generate screen text]
  Options: language / tone [on demand]
  Pending text in scene editor → Apply / Discard · Compare

  Selected scene
    Headline
    Supporting text, when enabled
    Metadata [on demand, when relevant]

Post caption [collapsed initially]
  [Generate post caption]
  Suggestion → Apply / Discard
  Editable caption
```

Expanding the caption section does not call a provider. Keep the exact two button
labels after first generation; loading state may indicate work in progress.

### Screen-text generation rules

- Snapshot source, stable scene IDs/order, effective durations, enabled text
  elements, typography/layout constraints, language, and text amount.
- Compute reading and layout limits before requesting copy. Less / Standard /
  More choose targets within those limits, rather than overriding them.
- Initially map the labels to existing short / standard / detailed preferences.
  Do not expose a second competing word-count setting. A numeric target can be
  added later if needed; it must share the same budget calculation.
- Count headline, supporting text, and visible metadata together. Repeated photo
  cycles do not create additional unique generated scenes.
- Disabled elements receive no new generated copy. Preserve their existing text
  for re-enabling; exclude it from visible reading limits.
- Generate category/source only where relevant. Source attribution comes from
  supplied source data; do not invent publisher, date, urgency, or factual claims.
  Brand/logo are user settings, not generated metadata.
- Return content-only patches keyed by scene ID. Generation cannot change media,
  fonts, layout, animation, duration, or audio.
- Review suggestions before applying. Applying changes screen text/metadata only.
- Duration, layout, and text-amount edits run local fit checks, never an automatic
  provider request. Users use Generate screen text again if they want a rewrite.
- For automatic timing, calculate a stable proposed timing/text pair using the
  existing timing policy. Show any proposed timing change explicitly for review;
  avoid a feedback loop where generation silently changes its own time budget.
- Retain bounded fit correction using the same screen-only schema. Do not trim
  arbitrary trailing words to force a factual sentence to fit. Report remaining
  fit problems while preserving the existing draft.

### Post-caption generation rules

- Use the source as evidence and the latest saved screen text as editorial
  context. Edited screen text is not independent factual evidence.
- If the user is posting a completed render, use that render's scene snapshot,
  clearly identified as the caption target. Later unrendered scene edits must not
  silently become the context for a different video's caption.
- A request returns caption text and optional normalized hashtags/source link;
  it does not return or apply scene text, template, music, or styling suggestions.
- Review before replacing a manually edited caption. Failed or cancelled work
  preserves the previous caption.
- Keep captions editable and persist them independently of video encoding. Adding
  or changing a caption after rendering must not require rendering again.
- Posting handoff explicitly pairs a selected completed video with its reviewed
  caption. Do not blindly reuse the caption captured when rendering began.
- Screen edits can mark a previously generated caption as based on older content,
  but never clear it or trigger regeneration. Caption readiness is separate from
  whether the rendered video matches the current visual composition.

## Modular composition design

Introduce a new schema version, provisionally version 4, independent of preset
identity. Finalize its exact shape before implementing the renderer.

```text
Reel document
  canvas: ratio, dimensions, frame rate
  shared style: font tokens, colors, branding
  scenes[]: stable ID, media reference, duration, arrangement, elements[]
  reel elements[]: progress line, repeated branding
  audio: music and prepared narration references
  preset provenance: optional starting preset ID/revision

Editorial draft, stored separately
  source and language
  text amount
  screen generation provenance / fit status
  post caption and its source / content / render target provenance
```

Use typed element definitions with a small internal registry: defaults,
validation, settings editor, layout measurement, and rendering behavior. This is
an internal extension mechanism, not a third-party plugin system.

| Feature | Owner and behavior |
| --- | --- |
| Headline | Scene element: content, typography, position, background, entrance |
| Supporting text | Optional scene element with independent appearance |
| Category/source | Optional metadata elements with explicit content provenance |
| Photo | Scene media: crop, fit/fill, transform and movement |
| Progress line | Reel element: visibility, position, color, thickness and progress style |
| Logo/brand | Reusable optional elements with shared defaults |
| Font | Shared style token with per-text-element override and language fallback |
| Animation | Settings on the affected element; transition belongs to scene boundaries |
| Background | Scene color/image/blur treatment; text-panel background belongs to text |
| Subject background removal | Optional media operation producing a derived transparent asset |

Presets populate ordinary settings/elements once. Subsequent controls work the
same across presets. Applying another preset must explicitly disclose the
appearance changes and preserve content, media, and caption.

Use a shared layout pass for text fit, safe areas, and overlap. Optional elements
can be added/removed without requiring another complete template. Removing an
element preserves its editable content in draft history/undo. Movement changes
must not reset unrelated typography or text entrances.

“Remove background” is ambiguous: hiding a text panel and removing a photo's
subject background are different operations. Support panel/background controls
in the core redesign. Treat subject cutout as a later capability with an explicit
provider/runtime decision; retain original assets and never imply it exists yet.

## Implementation phases and completion gates

### Phase 0 — Finish the UI/UX specification before implementation

- Map every current control, explanation, and status to Keep / Move / On demand /
  Remove using the inventory above. Cover Story, Media, Style, preview, export,
  header/drafts, narration, and music.
- Prepare low-fidelity layouts for empty, editing, pending suggestions, rendering,
  completed, stale, and error states. Include a returning user's saved workspace
  and a narrow-window layout.
- Review common repeated tasks: regenerate screen text, adjust one photo, change
  one element's style, and prepare a caption for a completed video. Identify
  duplicated controls, recurring messages, and unnecessary expansion steps.
- Finalize control placement and concise labels before application changes.
  The current request updates this plan only; do not begin implementation.

Gate: all Reel Studio surfaces have a defined default state and on-demand states,
with no unresolved duplicate ownership of settings or repeated instructional copy.

### Phase 1 — Split the two generation actions

Deliver the immediate workflow improvement on existing compositions first.

- Add separate screen-text and post-caption request/response schemas, prompts,
  validators, and authenticated local API endpoints. Reuse provider transport
  and factual-validation helpers rather than duplicating service infrastructure.
- Refactor `NewsAssistant.tsx` into source, screen-text, and post-caption sections.
  Keep exactly the two agreed generation actions; remove extra adapt/shorten
  generation controls from Story. Apply the concise Story layout and contextual
  feedback rules immediately rather than retaining the existing explanatory cards.
- Split request fingerprints and pending suggestions by action. Include text
  amount and all fit-relevant settings in screen fingerprints. Caption edits do
  not invalidate screen suggestions, and screen responses cannot write captions.
- Reject stale responses for the target they would replace. Different-action
  edits are preserved. Cancel or ignore responses after draft/target changes.
- Update editorial persistence and posting handoff for post-render captions.
  Preserve old endpoints until callers have migrated; the new UI uses only the
  separated contracts.

Gate: repeatedly generate/apply screen text, render, then generate a caption and
add that exact video/caption pair to a posting draft without another render.

### Phase 2 — Introduce versioned modular data and compatibility

- Define matching TypeScript/Pydantic schemas and validation for scene elements,
  shared styles, animation, and stable scene IDs.
- Separate content patches from appearance patches. Define fit measurement and
  shared-style inheritance explicitly.
- Keep existing version 1–3 drafts and completed job snapshots on their original
  renderer. Do not rewrite saved compositions on load.
- Offer explicit conversion by creating a new draft, preserving the original.
  Map supported features; disclose unsupported differences before conversion.
- Add capability negotiation for the new schema and elements so an older local
  backend produces a clear update message rather than losing fields.

Gate: legacy drafts still open/render as before; modular drafts survive save,
reload, scene reorder, and aspect-ratio changes without losing element identity.

### Phase 3 — Build the shared modular renderer

- Add shared scene layout and reusable photo/text/metadata/progress/brand drawing.
- Use the same measured scene description for posters, motion previews, and
  final exports. Preserve existing ratio-specific framing and language fallback.
- Move timing, text fit, progress, and animation out of preset-specific branches
  for new compositions. Keep legacy renderer branches isolated.
- Retain immutable render snapshots, local asset references, cancellation,
  deduplication, stale-preview protection, and output verification.

Gate: representative combinations render consistently across all supported
ratios, with readable Khmer/English text, correct timing, and optional elements.

### Phase 4 — Ship the modular editing flow

- Make scene text live in Story; remove duplicate editing ownership in Media.
- Apply the full tab/surface inventory above, including audio placement, header,
  quiet status, contextual errors, preview toolbar, and compact export settings.
- Add a compact element list, Add element, and a contextual settings panel.
  Start with guided arrangements instead of unrestricted layer positioning.
- Expose headline, supporting text, progress, branding, fonts, backgrounds, and
  element animation independently. Provide small visual choices with defaults.
- Replace template-gated controls for new drafts with shared controls. Keep
  presets as optional starters and legacy controls for legacy drafts.
- Use shared `Select.tsx` and `Checkbox.tsx`; do not introduce native dropdowns
  or checkbox inputs. Preserve keyboard access and a usable narrow-window layout.

Gate: a user can build a reel, remove supporting text, change fonts, adjust the
progress line, and change photo movement without resetting unrelated choices.
Returning users can complete these tasks without repeated onboarding paragraphs,
full inactive forms, duplicate status banners, or repeated reopening of help.

### Phase 5 — Extend and retire duplication

- Add optional subject background removal only after selecting and validating
  its runtime, transparency handling, caching, cancellation, and asset lifecycle.
- Convert useful existing looks into modular starter presets.
- Remove obsolete controls/code only after all active callers migrate. Retain
  the legacy reading/rendering paths required by saved drafts and render jobs.
- Update `docs/REEL_CREATOR.md` to describe the shipped flow and real limits.

## Validation and acceptance

Focus tests on boundaries where silent changes would lose work:

- Screen requests and provider responses contain no generated post caption;
  applying them preserves an existing caption byte-for-byte.
- Caption requests cannot mutate scenes, duration, media, styling, or audio.
- Only the two agreed Story generation actions appear; generation occurs only
  after an explicit click, including after settings changes and reloads.
- Text amount, scene durations, enabled elements, and actual text space affect
  the generation budget. Disabled elements stay disabled after Apply.
- Source grounding, unsupported numbers, provider errors, cancellation, and
  stale requests preserve existing user work.
- Manual changes made during generation cannot be overwritten by a late response.
- Caption changes after rendering do not invalidate the video or launch encoding.
  Handoff appends the chosen video/caption once without replacing posting drafts.
- Converted drafts preserve originals, crop/framing, text, brand, audio, and
  captions; unsupported mappings are visible. Legacy snapshots remain unchanged.
- Element combinations are visually inspected in posters and real encoded
  previews: long/short text, no text, hidden supporting text, progress positions,
  font fallback, aspect ratios, repeated scenes, and prepared narration.
- Inspect every tab in a returning-user session: only relevant controls and state
  remain visible; shared settings have one owner; optional/help expansion is
  remembered; no routine success or background check produces persistent clutter.
- Test keyboard access to tabs, scene selection, contextual settings, on-demand
  help, suggestion review, and actionable errors. Keep readable labels and focus
  when panels open/close, scenes reorder, and jobs complete.
- Verify errors and stale-video/caption targets remain understandable after copy
  reduction. Short labels must not hide an actual blocker or overwrite warning.

Run focused backend/frontend tests for each phase, then the relevant existing
Reel suites, type checking, and production build before shipping the changed
flow. Record real visual/render checks separately from mocked provider tests.

## Recommended first delivery

Finish Phase 0 first; this update authorizes planning only. When implementation
is requested, start with Phase 1 to solve the repeated-generation problem and
establish separate content contracts that the modular editor will reuse. Then
deliver Phases 2–4 as the core modular redesign, applying the shared UI/UX rules
to each changed surface. Phase 5 is an extension, not a prerequisite for the
simpler workflow.

Respect the existing working-tree changes: this plan adds a new document and
does not replace previous plans or modify application code.
