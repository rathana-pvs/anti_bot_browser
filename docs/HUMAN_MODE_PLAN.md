# Human mode implementation plan

Status: core local implementation added on `codex/human-mode-caption-hooks`.

Implemented: shared whole-caption entry policy; strict paste; stable session pacing;
clipboard draft/readback checks; focus/pointer/cancellation guards; review budgets;
Photo/Reel/Live/comment integration; text-only hook generation, preview, approval, and
immutable local scheduling snapshots. Tests use offline input/provider fixtures.

Remaining validation: real Docker/X11 composer calibration and a provider pilot.
No live posts or provider calls were made during implementation. Multiline/Unicode
captions conservatively use paste; there is no validated multiline typing mode.
X11 focus/pointer checks cannot detect all manual keyboard activity. Optional media
analysis remains a later phase. Cloud integration was not verified because the
sibling repository was unavailable. The resource-control consumer now tolerates
an omitted `ocr_engine.locked` field, preserving the pre-existing type edit while
retaining compatibility with older backend responses that include a lock.

## 1. Goal and agreed rules

Provide consistent, context-aware interaction pacing for local posting automation.
Randomness affects the manner of entry and bounded timing, never the requested
content, destination, attachments, publish count, or recovery decisions. These
settings are product defaults for validation, not measured human behavior or a
guarantee against platform detection.

Each caption uses exactly one method: full typing or full paste. There is no
type-and-paste combination, no deliberate typo insertion, and no automatic text
rewriting.

Policy precedence:

| Condition | Method | Reason |
| --- | --- | --- |
| Empty caption | Skip entry | No text requested |
| Dedicated link field or link comment | Paste | Links are paste-only |
| Caption containing a link | Paste entire caption | Preserve link and surrounding text |
| Caption with 100 or more words | Paste entire caption | Long captions are paste-only |
| Input backend cannot reliably type all characters | Paste entire caption | Preserve Unicode and formatting |
| Caption with fewer than 100 words, otherwise eligible | Choose full type or full paste | Initial probability: 50% each |

The 50% probability is an initial engineering default; the user has specified
the length and link rules, but has not specified a probability. Keep it in the
versioned policy rather than adding dashboard settings in the first release.

Count words using whitespace-separated tokens in version 1. Preserve the original
text; do not trim or normalize it for insertion. Test 99, 100, and 101 words.
Whitespace counting is imperfect for Khmer and other scripts without spaces;
their unsupported typing capability routes them to paste regardless. Document
this limitation rather than claiming language-aware counting.

Detect explicit HTTP/HTTPS URLs, www-prefixed links, and credible bare domains
with optional paths. Handle surrounding punctuation, newlines, mixed case,
Unicode domains, and URLs embedded in sentences. Use an existing maintained
parser if available; otherwise isolate a conservative detector with fixture tests.
Do not fetch links or use domain-specific allowlists. Explicit link-field context
forces paste regardless of detector output. When uncertain whether text contains
a link, prefer paste.

## 2. Existing implementation and gaps

- `automation/engine/behavior_profile.py`: fast/medium/slow presets, independent
  seeded random streams, configuration loading, and a behavior version already
  exist. Duration scaling currently samples independently for each call.
- `automation/engine/human_input.py`: mouse paths, press/release timing, typing,
  and scrolling already exist. Typing has per-character hesitation; safe click
  variation still uses global randomness.
- `automation/tasks/base_task.py`: creates a behavior session and exposes
  `paste_text()`. Its exception handler silently falls back to typing. That
  conflicts with paste-only rules. The behavior session currently receives a
  fresh UUID during task construction, not a durable execution identity.
- `automation/tasks/facebook_post.py`: pastes non-ASCII captions or captions over
  80 characters, otherwise types with a separate random WPM. It also has global
  random sleeps and a fixed-range review pause.
- `automation/tasks/facebook_reel.py`: always pastes captions and checks visual
  caption presence. It has a primary-selection paste fallback using Select All.
- First-comment paths share `BaseTask.post_first_comment()`. Keep these paste-only
  initially, including standalone comment execution, to avoid changing unrelated
  comment behavior.

The sibling `../main-server` repository and its required shared context files
were unavailable during planning. This plan is local. Any later shared settings,
protocol fields, or cloud behavior need a separate cross-project contract review.

## 3. Module design

### Existing `engine/behavior_profile.py`

Own presets, policy version, baseline session pace, typing WPM, and independent
random streams. Add `entry_strategy` and `review` channels. Choose session WPM and
pace once; vary gently around those baselines for phrases and individual actions.
Do not multiply a duration by both old and new scaling policies.

An execution session means one automation attempt, not the lifetime of a browser
profile. Keep baseline values stable throughout that attempt.

### New `engine/text_entry_policy.py`

Pure decision logic with no browser or clipboard side effects:

```python
choose_entry_plan(text, field_kind, capabilities, behavior) -> EntryPlan
```

`EntryPlan` contains method, reason, word count, link presence, policy version,
and typing WPM when relevant. The full text remains in memory for execution;
telemetry must not serialize it. No mixed method is represented in the type.

Evaluate forced-paste conditions before sampling the random strategy. Select
once per caption field and reuse the plan if the same field is revisited. Do not
reroll when a locator retries or the composer layout changes.

### New `engine/behavior_timing.py`

Calculate bounded durations from the selected session parameters and context.
Use triangular distributions with a typical value more likely than extremes.
Keep this module pure; screen recognition and sleeping belong elsewhere.

### Existing `engine/human_input.py`

Execute movement, clicks, and complete-caption typing. Apply phrase pacing without
changing characters. Route behavior randomness through session streams. Use an
explicit tested capability check before choosing typing; do not infer support
solely from text length. Verify special ASCII characters, literal shell-like
strings, newlines, and punctuation through the existing command boundary.

Require a tested line-break action for each editable field type. Never assume
bare Return inserts a newline: it may submit a comment or activate a control.
Keep `insert_line_break` separate from `submit`, with submission owned by the
existing task gate. If a field's newline behavior is unknown, exclude typing for
multiline content before entry and use full paste. Validate multiline paste too;
if safe insertion cannot be established, stop before input.

### New `modules/composer_entry.py`

Coordinate confirmed target, entry plan, execution, and task-specific verification:

```python
enter_caption(text, target, plan, verifier, execution_context) -> EntryResult
```

The task supplies the locator and verifier; this module does not assume that Post
and Reel composers have identical layouts. Return structured results such as
`entered_verified`, `entry_unconfirmed`, `clipboard_failed`, `focus_unconfirmed`,
and `cancelled`. Distinguish definitely-no-input failures from possibly-partial
entry, so callers cannot blindly insert the full caption again.

Before insertion, classify the current field as `empty`, `matches_requested`,
`different`, or `unknown`. Insert only into a confirmed empty field. Skip insertion
when the requested caption is reliably established as already present; record
`reused_existing_content` and still perform destination/media checks. Different
or unknown content requires review, with no automatic clearing or appending.
OCR resemblance alone cannot establish exact equality or justify overwriting a
draft. Apply this check to restored drafts and revisited Reel composers.

Guard input ownership before each dispatch and at phrase boundaries: confirm the
intended browser/composer is still active, check cancellation or takeover signals,
and stop pending dispatches on focus loss, composer closure, or operator takeover.
Use available signals without claiming reliable detection of all manual input;
when ownership cannot be established, return an explicit interrupted/uncertain
entry outcome. Bound already-dispatched input; do not queue the whole caption
in an uninterruptible batch. Do not steal focus back or resume automatically.
Keep partial text for review and use existing task outcome/recovery handling;
this must not create a retry that reinserts text blindly.

### New `modules/composer_review.py`

Coordinate content/destination/attachment checks and a bounded review pause.
Call existing recognizers rather than building a second state machine. A pause
is not evidence that content was verified, and never satisfies a manual gate.
After the pause, recheck the relevant state and final action target.

### Existing tasks, publish gate, and backend

Tasks continue to own screen sequence, layout branching, outcome mapping, and
publication verification. Preserve the existing guarded publish operation and
comment submission lock. Reuse account/profile locks and resource admission.
No changes to leases, fencing, cloud events, or queue scheduling are planned.

## 4. Proposed timing defaults

Medium-mode starting ranges, before validation:

| Action | Minimum | Typical | Maximum |
| --- | --- | --- | --- |
| Observe a ready entry page, once | 0.6 s | 1.0 s | 1.5 s |
| Focus settle | 0.15 s | 0.25 s | 0.4 s |
| Mouse travel | 0.15 s | Distance-dependent | 0.8 s |
| Mouse hold | 0.05 s | 0.08 s | 0.12 s |
| Phrase pause | 0.2 s | 0.35 s | 0.7 s |
| Sentence pause | 0.4 s | 0.7 s | 1.2 s |
| Settle after confirmed paste | 0.2 s | 0.35 s | 0.6 s |
| Inspect each ready image | 0.8 s | 1.2 s | 2.0 s |

Keep the existing preset WPM ranges: fast 58–72, medium 45–62, slow 32–48.
Sample baseline WPM once. A phrase may vary by approximately 10% around that
baseline, bounded to the preset range. Apply at most one boundary pause rather
than stacking word, phrase, and sentence pauses at the same position.

Initial review base duration:

```text
clamp(1.5 + 0.08 * word_count + 0.7 * image_count, 2, 12) seconds
```

Apply small bounded variation and clamp the result again. Cap separate image
inspection at six seconds total. Do not add these pauses repeatedly on locator
polls or every time a task observes the same ready state.

Account for review with one per-attempt ledger keyed by caption/media revision.
Count completed media inspection toward the image portion of the final review
budget, rather than charging for the same images twice. Revisited layouts do not
restart the budget; changed content invalidates only the affected review checks
and requires a bounded recheck. A short pasted caption uses the same content-based
budget as a typed caption, without an extra artificial typing-length delay. Keep
mandatory verification and manual gates independent of this optional pacing budget.

Measure actual entry throughput with a monotonic clock. Record active dispatch
time, intentional pauses, and total elapsed entry time separately. Docker and X11
command latency already consume part of each target character interval; sleep
only for its nonnegative remainder. Never compensate for slow commands by sending
a burst of overdue characters or skipping phrase boundaries. Effective WPM is
`(character_count / 5) / elapsed_minutes`, reported as an input convention rather
than a language-independent reading measure. Use a local non-publishing fixture
for initial calibration; exclude fixture overhead from runtime behavior sampling.
If transport latency makes a target WPM unattainable, report the observed slower
rate and use it for deadline estimates. Do not silently change the entry method.

Readiness waits use observed screen conditions and existing task deadlines, not
these ranges. Upload, transcode, login, and publication timeouts remain owned by
their existing flows. Preserve Reel caption entry during media processing when
that layout supports it; do not force an unnecessary serial upload wait.

Use cancellation-aware waits and a monotonic clock. Include entry and review
duration in the execution deadline. Slow typing can take several minutes; verify
that progress reporting prevents false worker-silence recovery. If the selected
method cannot fit the remaining budget, stop before input with an explicit reason
rather than silently switching methods or interrupting content halfway through.

## 5. Action sequences

### Photo/text post

1. Acquire existing profile lock and resource slot; perform existing login checks.
2. Confirm destination and observe the ready page once.
3. Open the existing media or text composer route.
4. For media, complete the file chooser and wait for confirmed media readiness.
5. Confirm the caption field, inspect existing draft content, and create or
   retrieve its entry plan. Stop on different/unknown content; skip insertion
   when matching requested content is reliably confirmed.
6. For an empty field, focus and settle; execute full typing or full paste exactly
   once, with field-specific line breaks and interruptible ownership checks.
7. Verify entry using the task's supported evidence; classify uncertainty honestly.
8. Follow the existing Next/review screen branch if required.
9. Check destination, media, and caption; apply review pause once.
10. Reconfirm final target and call the existing publish gate.
11. Begin publication verification immediately. Never delay verification for style.
12. Execute an optional first comment through the existing guarded path, using paste.

### Reel

Use the same entry policy at the existing caption-entry opportunity, including
early entry during processing. Keep `_reel_caption_entered` and the selected plan
stable across early/final composer branches to avoid duplicate insertion. Preserve
existing template routing and publication verification.

### Link comment or dedicated link field

Confirm target → focus → strict full paste → verify insertion → existing submit
gate once → verify submission. No typing fallback, including on clipboard failure.
Inspect existing content before paste; stop on conflicting or unknown drafts.
Newlines in the pasted payload must not invoke the submission action.

## 6. Clipboard and verification failure rules

- Audit every `paste_text()` caller before changing helper semantics.
- Make paste failure explicit; the strict helper must never call `type_text()`.
- A clipboard write failure before the shortcut means no paste was dispatched.
  A bounded clipboard retry is permitted after focus is reconfirmed.
- If the shortcut was dispatched but insertion is uncertain, inspect the field.
  Do not assume it is empty and do not append the entire caption again.
- An alternative paste shortcut may be used only after confirming the intended
  editable field and a safe replacement/empty-field state. Do not blindly Select
  All when focus is uncertain. Keep the same paste entry plan.
- Partial typing does not switch to paste. Stop for a controlled recovery or review.
- OCR presence is not exact text equality, particularly for long text or Unicode.
  Report verification strength and reuse existing review gates when exact content
  cannot be established. Do not claim byte-exact verification from screenshots.
- No recovery path may republish or resubmit a possibly completed public action.
  Preserve existing uncertain-publication status and durable publish markers.

## 7. Reproducibility, progress, and telemetry

Record policy version, entry method, selection reason, word count, link flag,
session WPM, verification strength, planned/actual duration, and outcome. Keep raw
captions, clipboard payloads, and URLs out of newly added behavior telemetry.
Include observed effective WPM, dispatch overhead, intentional pause duration,
draft disposition, interruption reason, and consumed review budget. Do not log
existing draft content or unrelated operator input.

Record the selected plan before entry in existing local evidence metadata. Inspect
the runner's available attempt/run identity before replacing the constructor UUID.
Reuse an existing stable identity where available without adding protocol fields.
If durable plan reuse across restart is unavailable, report that limitation; never
claim restart reproducibility or resume partially completed entry automatically.
Preserve existing recovery decisions instead of rerunning an interrupted composer.

Progress events during long entry should identify the phase and elapsed time,
without exposing caption text. Do not emit fake execution-stage transitions just
to keep a worker alive.

## 8. Implementation phases

1. **Policy:** add the pure planner, detection/capability rules, versioning, and
   independent strategy stream. Cover boundaries and deterministic decisions.
2. **Strict paste:** audit callers, remove typing fallback, propagate failure
   information, and test clipboard uncertainty without submitting anything.
   Add draft-state checks, field-specific newline capabilities, and focus/takeover
   interruption handling before enabling randomized typing in task integrations.
3. **Timing:** add stable session baselines, bounded sampling, phrase pacing,
   cancellation/deadline handling, progress compatibility, measured transport
   overhead, and the shared review-budget ledger.
4. **Photo/text integration:** replace the 80-character rule and explicit random
   WPM, integrate readiness/entry/review, and preserve publication guards.
5. **Reel/comment integration:** share policy for Reel captions; preserve early
   entry and alternate paste handling; enforce paste-only comments and links.
6. **Verification and documentation:** run focused regression checks, update
   README behavior claims, and validate in a local non-publishing composer fixture.

Do not introduce new human-mode timing controls in the first implementation. Reuse
existing behavior presets and provide one versioned default policy. The separate
hook preparation feature in section 11 requires preview and selection controls;
use the repository's shared Select and Checkbox components where applicable.

## 9. Acceptance checks

- 99-word eligible captions can select either whole-caption method; 100/101-word
  captions always select paste.
- Links force paste at any length, including embedded links and explicit link fields.
- Unsupported Unicode typing always selects paste before input; text is preserved.
- No plan can contain both typing and pasting. No clipboard failure invokes typing.
- Fixed seed, policy version, and execution identity produce the same plan;
  consuming mouse randomness does not change it. Locator retries do not reroll it.
- Controlled draws exercise both short-caption methods without flaky statistical tests.
- Timing stays inside bounds; cancellation stops input; no timeout triggers a duplicate.
- Post and Reel callers use the same policy; Reel branch transitions do not reinsert.
- Incorrect focus, partial insertion, and ambiguous verification stop safely.
- Multiline typing and pasting preserve line breaks in supported field types;
  neither operation submits a comment/post or bypasses the submission gate.
- Matching restored drafts skip insertion; different or unknown drafts stop for
  review without clearing, appending, or submitting. Test weak OCR evidence too.
- Simulated focus loss, composer closure, cancellation, and takeover stop further
  input at the next guard boundary; there is no automatic refocus or resumption.
- Simulated Docker latency reduces remaining sleeps without causing catch-up
  bursts; observed WPM and deadline estimates include transport overhead.
- A revisited composer does not duplicate review pauses. Media inspection consumes
  its existing budget; changed content triggers only the relevant bounded recheck.
- Existing publication/comment single-submit, uncertain outcome, and recovery tests pass.
- Fake clock/input tests avoid actual sleeps. Local composer fixtures cover literal
  special characters, multiline text, emojis, Khmer, and paste failure. No live
  publication is needed to validate this behavior layer.

## 10. Scope and completion

The human-mode implementation is complete when the shared policy, strict paste execution, bounded
pacing, task integrations, telemetry, and focused checks satisfy the criteria above.
Real-world timing calibration remains a separate measured follow-up. Unrelated browser navigation and feed-warming behavior are outside
the first release; shared-helper callers must still handle paste errors explicitly.

Respect the existing unrelated modification to `manager-app/src/types/automation.ts`.

## 11. Optional caption hook preparation

### Purpose and boundary

Generate opening-sentence candidates for each post before scheduling. This is a
separate content-preparation feature, not a behavior performed inside the browser.
It is optional per post or batch and disabled by default. Disabling it preserves
the original caption. Generation and preview do not authorize publishing.

Initial output: three short hook candidates per post. The operator selects or
edits a candidate and previews the complete final caption before scheduling.
Keep original caption, generated candidates, and approved final caption distinct.
Default composition prepends the selected hook and a paragraph break to the
unchanged original caption. Any edit to the body must be explicit in the preview.

### Data sources and precedence

| Source | Available information | Use and limitations |
| --- | --- | --- |
| Original caption | Main message, supplied facts, offer, call to action | Primary source for claims; preserve required text and links |
| Optional post description | Product/event facts supplied by operator | Use when original caption lacks context |
| Campaign settings | Audience, language, tone, business/product context | Apply relevant campaign defaults with per-post overrides |
| Hook instructions | Preferred style, required wording, words to avoid | Constrain the output; do not override factual limits |
| Selected image analysis, later phase | Visible subject, scene, product appearance | Visual context only; not proof of price, stock, origin, quality, or offers |
| Selected video analysis, later phase | Bounded frame samples and available transcript | Describe observed content; retain uncertainty and source provenance |
| Previously approved hooks | Style examples and recent wording | Avoid repetition; never treat examples as current product facts |

Start with original caption, optional post description, and campaign settings.
Image/video analysis is a later optional enhancement, not a prerequisite for text
generation. Use only operator-selected media for the post. Do not scrape browser
profiles, private sessions, unrelated files, or external pages to fill gaps.

Per-post settings override campaign style defaults. Current per-post factual
information takes precedence over generic background; conflicting facts require
clarification rather than choosing silently. If there is too little information,
return `needs_context` with a request for a short description, leaving the original
caption intact. Captions, transcripts, and media text are data, not instructions
that can override generation constraints.

### Generation contract and modules

Proposed backend module: `backend/services/caption_hook.py`. Keep provider calls
outside automation input modules and reuse the configured provider adapter after
inspecting its actual capabilities. No provider is assumed to support vision.

```python
build_hook_context(post, campaign, approved_history) -> HookContext
generate_hooks(context, provider) -> HookCandidates
validate_hooks(context, candidates) -> ValidationResult
compose_final_caption(original_caption, selected_hook) -> str
```

`HookContext` contains original caption, optional description, audience, language,
tone, required text, prohibited wording, source provenance, and bounded recent
approved hooks. Only send fields needed for generation. Never send browser
cookies, credentials, profile directories, or unrelated account data. The UI
should explain that supplied caption/context and any enabled media analysis go
to the configured generation provider. Do not log provider payloads or secrets.

Return structured candidates with stable IDs and explicit statuses such as
`ready`, `needs_context`, `validation_failed`, and `provider_failed`. Generate
hooks only, rather than asking the provider to rewrite the entire caption. The
application composes the body deterministically. Preserve exact URLs and required
wording, including query parameters and formatting. Do not resolve or shorten URLs.

Generated hooks must not invent prices, discounts, availability, testimonials,
product claims, urgency, or guarantees. Language and tone should follow the chosen
settings, including Khmer or English. Validate structured output, length, prohibited
wording, duplicate candidates, and protected text. Automated validation cannot
prove all claims true; the full-caption preview is still required.

Compare candidates against other hooks in the batch and bounded recent approved
history. Start with normalized text similarity and exact duplicate checks; use
language-aware similarity only if available and validated. Permit at most one
automatic regeneration pass for invalid/duplicate candidates. If three usable
options cannot be produced, show the available options and a clear status rather
than looping or inventing extra facts.

### Preparation sequence and saved state

```text
Create post and select media
  → optionally enable hook generation
  → build context from supplied post/campaign data
  → generate and validate up to three candidates
  → operator selects/edits and previews full caption
  → save approved final caption and revision
  → schedule an immutable caption snapshot
  → human-mode policy selects type or paste from that final caption
  → existing guarded posting flow
```

Save original caption, source revision, candidate IDs/text, selected or edited
hook, approved final caption, approval revision, and generation/prompt version
using the existing local post/batch storage after inspecting its schema. Provider
model metadata may be saved without secrets. These are explicit content records,
not raw behavior telemetry.

Changing the original caption, facts, relevant campaign settings, selected media,
or hook after approval invalidates approval and requires another preview before
scheduling the changed version. In-flight generation results must match their
source revision before being accepted; discard stale results. Regeneration is
explicit after the bounded validation retry and never occurs during execution.

Posting retries reuse the scheduled final-caption snapshot. They never regenerate
a hook. Adding a hook can move the final caption across the 100-word boundary;
count the full final caption. Any link in it forces whole-caption paste. Review
timing also uses the final content. Existing draft matching compares against this
same snapshot.

For a batch, generate per distinct post, not per execution retry or account
assignment. All assigned profiles use the approved caption for that post unless
the operator explicitly creates and approves separate variants. Show failures
per post without overwriting successful approved content. A failed generator
must not silently schedule unreviewed output; offer retaining the original caption
through an explicit selection, or leave preparation incomplete.

### Delivery phases and acceptance checks

1. Inspect current post/campaign persistence, configured generation provider, and
   caption preview UI. Confirm local ownership before schema/API changes. Recheck
   shared context if cloud jobs or contracts are affected; the cloud repository
   was unavailable during planning.
2. Implement text-only context construction, structured generation, validation,
   deterministic composition, and bounded retries using provider fixtures.
3. Add opt-in controls, three-candidate preview/selection, editable final preview,
   revision checks, and persistence. Use shared UI components.
4. Connect only approved caption snapshots to existing scheduling and human-mode
   entry. Test boundaries, stale responses, failures, and execution retries.
5. Add optional image analysis, then bounded video frames/transcripts, after
   confirming provider support and resource/request limits. Do not download linked
   media automatically or claim visual observations establish business facts.

Acceptance checks:

- Disabled generation preserves the original caption exactly.
- Supplied text/context alone can produce preview candidates; missing context
  yields `needs_context` without fabricated facts.
- Links, required text, and the original body survive composition unchanged.
- Selection/editing and full preview precede scheduling generated content.
- Modified sources invalidate approval; stale responses cannot overwrite newer work.
- Duplicate detection and regeneration are bounded; provider errors are explicit.
- Batch posts retain separate candidates and revisions; account assignments do
  not trigger unsolicited regeneration.
- Retries use the saved caption, with no provider call from the posting engine.
- Final-caption length and links drive the existing strict type/paste rules.
- Fixtures exercise Khmer/English, conflicting facts, malformed provider output,
  unavailable provider, duplicate hooks, and embedded untrusted instructions.
- No live generation, upload, or publication is required to test the contract;
  any later provider/media pilot is an explicit feature validation step.


## 12. Live integration extension

Live titles and captions now use the shared whole-field policy through
`automation/modules/live_input.py`; preset pinned comments are paste-only. Live
keeps its separate task instead of inheriting BaseTask because Producer can show
stream credentials. The adapter suppresses evidence capture and allows only safe
method/timing metadata in memory. It reuses profile behavior settings, bounded
review, draft checks, clipboard readback, and focus/cancellation checks.

The backend passes the existing local Live session identity for reproducible
behavior. The preparation deadline reserves time for Save and saved-value checks;
insufficient budget stops entry without changing its selected method. Existing
saved-details handling, publication/end gates, playback, stream duration, and
account/resource ownership are unchanged. An unrecognized empty pinned-comment
field stops preparation for review instead of guessing or overwriting content.

Verification uses offline field fixtures and existing Live browser/service tests.
The installed runtime's profile_004 session reported completed/broadcast_ended;
there were no saved Live screenshots, and its container was stopped. This is end
state evidence, not an input-layout pilot. No profile or broadcast was started.
Optional hook generation remains limited to Photo/Reel preparation.


## 13. Passive preparation scroll and mouse extension

Preparation now uses `engine/passive_browsing.py` to plan whole scroll bursts and
reading pauses from the existing session streams. These are starting product
settings, not measured human behavior or a detection guarantee:

- Downward bursts: 1–4 wheel notches; each notch keeps the existing input engine's
  variable timing. Short upward rereads: 1–2 notches after prior downward input.
- Reading pauses: triangular sampling (1.5 s minimum, 3.2 s typical, 6 s maximum),
  adjusted by session pace and clamped to 1–8 seconds and the remaining budget.
- Pointer: enter content before the first burst and whenever it leaves the
  content rectangle. Otherwise rest about 70% of the time; occasional movements
  remain near the current position. Existing curved mouse paths are reused.
- Response: compare the scrollable region rather than the static browser toolbar.
  Two unchanged checks permit one upward boundary check; further unchanged
  observations stop browsing early. This does not prove the page's scroll offset
  or that the operator read the content.
- Handoff: return upward in 2–4-notch bursts, capped by an eight-second budget
  plus input already in progress. Report remaining requested offset; do not claim
  top-of-page confirmation.
- No clicks, likes, reactions, comments, or publication actions are added. Profile
  locks, scheduler admission, and publish gates retain their existing ownership.

The browsing timer now uses a monotonic clock, progress logs name each action and
remaining time, and evidence separates requested actions from observed changes.
Offline tests cover bounds, quiet pointer periods, independent streams, rereads,
unchanged-page recovery, and task handoff. The changes were built and installed
in desktop version 1.1.4 on October 19, 2026. After an idle maintenance check, the
app was restarted, readiness passed, and active runtime files matched source.
The behavior has not yet been exercised on an active profile.

## 14. Reliability-first follow-up plan (proposed)

Status: planning only. Installed behavior remains unchanged. The objective is to
preserve posting correctness while making optional preparation interruptible and
its handoff verifiable. No completion-rate improvement is claimed without pilot
measurements. The sibling main-server repository is unavailable; this work stays
local and proposes no worker protocol or shared-state changes.

### Phase 1: establish boundaries and interruption behavior

- Audit Photo, Reel, and Live entry points, existing ownership checks, cancellation
  paths, and readiness recognizers before adding another abstraction. Reuse
  existing stage and outcome contracts; do not invent parallel job states.
- Restrict optional browsing to preparation. Composer, upload, submit, and result
  verification permit only task-required input. Existing whole-field entry policy
  and bounded review remain; optional input must never run concurrently with them.
- In facebook_preparation.py, check cancellation, ownership, and the monotonic
  deadline before each movement/scroll and during reading waits. Reuse or extend
  InteractionWait after checking its ownership assumptions. Guard individual
  mouse/scroll input chunks if the current input engine cannot stop mid-action.
- Cancellation prevents subsequent optional input; do not rewind or navigate
  during cancellation cleanup. Ownership loss stops the attempt. Do not introduce
  automatic pause/resume across arbitrary publishing states in this phase.
- Add an independent switch for optional browsing at the existing preparation
  configuration boundary. Disabled browsing still performs required session and
  handoff verification. Capture the setting per attempt; switching it affects
  future attempts without changing an attempt in progress.

Acceptance: cancellation during a reading wait dispatches no next input; a lost
owner cannot issue another action; optional browsing cannot run during composing
or publication. Document the measured interruption bound, including in-flight
input, rather than promising immediate cancellation.

### Phase 2: verify the preparation handoff

- Separate optional browsing failure from required session failure. An unchanged
  page or exhausted browsing budget ends optional browsing; it is not evidence of
  an authenticated, safe-to-post page.
- Use existing session/state recognizers to establish a known starting surface
  before reporting preparation ready. Publisher entry points recheck their own
  required state because a queued prepared profile may wait before publishing.
- Avoid navigation that could discard a draft. Unexpected composers, dialogs,
  authentication gates, or ambiguous drafts require the existing safe stop or a
  specifically recognized non-destructive recovery.
- Permit at most one safe pointer reposition/retry for a nonresponsive scroll,
  inside the current budget. Reuse the existing boundary logic where possible.
- Keep SSIM as an observed visual-change signal: video and animation may change
  pixels without scrolling. Do not label it confirmed scroll displacement or
  assume equal upward/downward wheel input returns to the original location.

Acceptance: reaching the browsing deadline can still reach publishing through a
verified handoff; login/checkpoint, unknown page, and conflicting draft cannot.
No blind refresh, draft deletion, or extra publication action is introduced.

### Phase 3: strengthen existing posting readiness checks

- Inspect current Photo/Reel upload and composer gates and Live saved-details
  gates; add checks only where coverage is missing. Use bounded polling with
  cancellation checkpoints and meaningful progress instead of extra fixed waits.
- Before submission, verify the applicable caption/title, media readiness, and
  expected enabled action using each workflow's existing mechanisms. Successful
  caption readback alone does not establish media or destination correctness.
- Preserve single-submit behavior and uncertain-publication outcomes. A timeout,
  restart, cancellation after submission, or lost connection cannot authorize a
  second submit. Preserve durable attempts, queue handling, and account locks.
- Keep Live separate: no preparation scrolling in Producer, no stream credential
  screenshots or logs, and no changes to Go live/End live or streaming duration.

Acceptance: delayed uploads wait within budget; incorrect text/media/state stops
before submit; ambiguous results remain uncertain without a second submit. Each
workflow has its own fixtures rather than assuming Photo coverage proves Live.

### Phase 4: modest behavioral tuning after the guards pass

- Reuse existing session pace and independent random streams. Keep the current
  whole-caption typing/paste decisions and link paste-only rule unchanged.
- Prefer pointer rest and movements connected to the next preparation action.
  Tune bounded pauses only from useful page observations; defer semantic reading
  and video detection until those signals can be validated reliably.
- No additional engagement actions, random clicks, intentional typing mistakes,
  or provider requests during execution.

### Validation, rollout, and rollback

1. Establish the available baseline from existing telemetry for Photo and Reel;
   use sanitized Live status/timing separately. Report sample sizes and unknown
   outcomes. Do not treat a few successful runs as a completion-rate guarantee.
2. Add offline failure fixtures for cancellation, ownership loss, unchanged and
   animated content, deadline expiry, stale prepared state, dialogs, conflicting
   drafts, slow uploads, and ambiguous submission. Run the relevant existing
   automation/backend suites and frontend checks if controls change.
3. Verify preparation alone on a selected idle profile without creating content.
   Real photo, Reel, and Live publication pilots require explicitly selected
   content, destination, and scope; this plan does not authorize publishing.
4. Enable optional browsing for a small selected set first. Review confirmed
   completion, failed-before-publish, uncertain outcomes, cancellation latency,
   preparation duration, handoff failures, and recovery attempts by workflow.
5. Stop expansion on any unintended submit, draft overwrite, ownership breach,
   credential exposure, or optional input after handoff. Investigate any increased
   failures or uncertainty before wider use. Disable optional browsing for future
   attempts and retain the last known-good package; never rollback by resetting
   active attempt records or republishing uncertain work.

Implementation order: phases 1 and 2 first, then readiness gaps from phase 3.
Phase 4 is optional and follows validation; it must not delay correctness fixes.
