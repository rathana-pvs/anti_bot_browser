# Automation structure: design and implementation plan

Date: 2026-10-30 · Revision: 1 · Status: Proposed. Nothing implemented.

## Recommendation

Split the Facebook automation into five layers with one rule: workflows and
skills never touch the driver directly; every input goes through the humanizer.
Share the Facebook steps that Post, Reel and Live repeat, keep the steps that
differ in small per-workflow stage lists, and improve human-likeness starting
with session shape, then navigation, then input detail.

```
Job shell      lock · session plan · evidence · outcome          shared
Workflow       Post | Reel | Live stage lists                    separate
Skills         Facebook steps every workflow reuses              shared
Humanizer      persona, mouse, typing, scrolling, dwell          shared
Driver         screenshot · tap · key · paste · put_media · open one per surface
```

| Choice | Decision and reason |
| --- | --- |
| Order | Structure first (driver, humanizer, skills), behavior changes after. Each structural step must replay existing evidence unchanged. |
| Live | Stays a separate *session* kind with its own lifecycle (encoder, start, end). It shares driver, humanizer, lock and the publish-gate rule, not the publishing pipeline. |
| Brain `workflow.yaml` | Becomes the real stage list for each workflow, or is deleted. Never both. |
| Randomness | Only from seeded `BehaviorSession` streams, so replays reproduce. Safety checks (publish gate, re-observation before clicks, uncertain handling) are never randomized. |
| Side-effect actions | No likes, reactions or follows added for realism without a separate design. |

## Repository findings

- `automation/tasks/base_task.py` (1,865 lines, ~49 methods) mixes session
  checks, navigation, dialogs, file chooser, comments, permalinks, evidence,
  outcome and the publish gate.
- `facebook_post.py` (962 lines) and `facebook_reel.py` (2,039 lines) each do
  their real work in one `run()`. `modules/publishing_pipeline.py` wraps it as a
  single `PublishModule`; later modules reach into private task methods
  (`_refresh_until_latest_reel_visible`, `_verify_latest_image_post`) and set
  `task.result_status` themselves.
- `brains/facebook_post` and `brains/facebook_reel` `workflow.yaml` are never
  executed; only their `config/` and `templates/` are read. `WorkflowInterpreter`
  runs only for Live, where the workflow is a three-state dispatcher.
- Tasks call `self.client.*` about 80 times; tasks, modules and engine contain 48
  raw `xdotool` calls.
- `FacebookLiveTask.click` and the Live modules click with
  `xdotool mousemove … click` (no curve, no timing). Typing goes through
  `LiveInput` → `HumanInput`.
- `ContainerClient.navigate_to` types URLs with raw `xdotool type` (fixed
  per-key delay). Jobs open `https://www.facebook.com/me` this way every run.
  `get_current_url` focuses the address bar and copies, which blurs the page.
- `BehaviorSession` has three presets that vary only pace and WPM.
  `facebook_warming.py` uses the global `random`, not the seeded streams.
- `HumanInput.move_to`: Bezier path, duration 0.2–0.7 s from distance only, ±1 px
  uniform jitter per step, constant step sleep, no overshoot. `safe_click_point`
  is used in 9 places; most clicks hit the OCR box center.
- Publish jobs start the container, verify login, publish and stop the container.
  `WarmingModule` exists in the pipeline but returns
  `inline_warming_not_configured`.
- `docs/ANDROID_DEVICE_PLAN.md` milestone 3 ("Driver refactor") depends on the
  same driver extraction as this plan.

## Layers

### Driver

Surface-neutral interface, the same one the Android plan defines:

```
screenshot · pointer_position · pointer_move(x, y) · button(down|up)
wheel(direction) · key(name) · type_char(c) · set_clipboard · paste
put_media(path) → handle · open_target(url|intent) · current_location
screen_size · input_owner
```

- `ContainerDriver` wraps `ContainerClient`. It is the only code that runs
  `xdotool`, `scrot` or `docker exec` for input or capture.
- `AndroidDriver` comes later (Android plan milestone 4).
- Primitives are low level on purpose: timing and paths belong to the humanizer.

### Humanizer

`HumanInput` grows into the single input API, built on a driver and a persona:

```
move_to · click(target | bounds) · double_click · type_text · paste_text
scroll · key_press · dwell(kind) · navigate(surface)
```

- `click` accepts bounds and picks the point itself; callers stop passing raw
  centers.
- `navigate(surface)` prefers UI navigation (Home, avatar, profile link) and
  falls back to typing the URL through `type_text`.
- `dwell(kind)` covers reading, review and idle waits with persona timing.

### Skills

Each skill is a class or function with a typed result, taking a context that
holds driver-facing humanizer, perception, evidence and telemetry. Moved out of
`BaseTask`:

| Skill | Today |
| --- | --- |
| `ensure_session` | `verify_logged_in`, `skip_unverified_session` |
| `go_to(surface)` | `navigate_to`, `open_warming_surface`, `select_and_open_warming_surface` |
| `dismiss_known_dialog` | `handle_leave_site_dialog`, `handle_post_publish_prompt`, `check_and_dismiss_post_prompt` |
| `attach_media` | `find_visible_file_chooser`, `attach_file_gtk` |
| `enter_text` | `ComposerEntry`, `text_entry_policy`, `paste_text` |
| `review_before_publish` | `ComposerReview`, `review_composer` |
| `publish_gate` | `execute_publish_gate`, `cloud_publish_guard` |
| `verify_on_profile` | profile scans, `post_timestamp`, `reel_top_post`, `reel_gallery_verifier` |
| `find_permalink` | `correlate_and_extract_permalink`, `recover_missing_permalink`, `validate_facebook_permalink` |
| `first_comment` | `post_first_comment`, `post_first_comment_with_page_reuse`, `post_reel_first_comment_after_refresh` |
| `browse` | `PassiveBrowsing`, the warming loop |

`publish_gate` takes a per-content-type observer instead of the current
`getattr(self, "_observe_direct_reel_publish_gate")` hook.

### Workflows

A workflow is an ordered list of stages. Each stage returns a `ModuleResult`;
the orchestrator runs them, stops on the first non-success, and always runs
Finalize.

| Workflow | Stages |
| --- | --- |
| Post | `open_profile_composer → attach_media? → wait_media_ready → enter_caption → review → publish → verify → first_comment?` |
| Reel | `route_reel_entry (t1–t3) → attach_media → handle_remix_dialog → next_to_share → enter_caption → review → publish → verify_gallery → first_comment?` |
| Live | `prepare_producer → fill_details → pin_comment? → [encoder] → go_live (gate) → monitor → end_live` |

- Only stages that differ live in the workflow; the rest call skills.
- `workflow.yaml` lists these stages and their transitions; the interpreter runs
  them. Templates and config stay where they are.
- Live keeps `live_service.py` for the encoder and lifecycle. Go Live uses the
  same gate rule as publishing: one click per execution, durable intent first.

### Job shell and outcome

- One `PublicationStatus` enum replaces string statuses spread across tasks and
  modules.
- Stages return results; only Finalize writes the task outcome.
- The shell owns the account lock, the session plan (below), evidence and
  telemetry setup, which `BaseTask.__init__` does today.

## Human-likeness

Ordered by expected impact. Each item ships behind the persona so it can be
compared in telemetry before becoming the default.

### 1. Session shape

- A `SessionPlan` per job, from the persona: optional pre-browse (feed or
  profile, bounded budget), the task, then a short linger (view own post,
  scroll). Implemented by enabling the existing `WarmingModule` slot and adding
  a linger stage after verification.
- Per-account active hours in the persona; the scheduler prefers them and keeps
  its existing stagger.
- Budgets are bounded and never run after an uncertain publication.

### 2. Navigation

- `go_to` clicks through the UI when the target is reachable from the current
  page; typed URLs go through `HumanInput.type_text`.
- `current_location` reads the URL from the screenshot (address bar OCR) where
  possible; the copy-from-address-bar path is a fallback with a per-run limit.

### 3. Persona

Stable traits per account, derived from `behavior_seed`, with small per-run
drift:

| Trait | Used by |
| --- | --- |
| pace, WPM (existing) | all timing, typing |
| pointer speed, overshoot tendency, path curvature | `move_to` |
| reading speed | `dwell`, review, browse |
| typo rate | `type_text` |
| scroll style (notches, re-read rate) | `scroll`, `browse` |
| type-vs-paste preference | `text_entry_policy` |
| active hours, linger tendency | `SessionPlan`, scheduler |

- Bump `BEHAVIOR_VERSION`; existing `behavior_mode` presets map to trait ranges.
- Warming and every other caller use session streams, never global `random`.

### 4. Mouse

- Duration from Fitts' law (distance and target width) plus persona speed.
- Overshoot and correction on long moves, by persona tendency.
- Smooth low-frequency noise instead of per-step uniform jitter; variable step
  intervals.
- Every click picks a point inside confirmed bounds; center only for small
  targets.
- Live clicks go through `HumanInput` (also covered by milestone 1).

### 5. Typing

- Inter-key delay varies by character pair and phrase; a short pause before the
  first key.
- Occasional typo and Backspace correction, only in fields whose final text is
  checked by OCR before publishing.
- Unicode and multiline text stay paste-only.

## Implementation milestones and exit criteria

### 1. Driver and humanizer boundary

`ContainerDriver`; `HumanInput` built on it; all raw `xdotool` calls removed
from tasks, modules and engine outside the driver; Live clicks and URL typing
go through `HumanInput`.

Exit: a test fails if `xdotool` or `exec_cmd` appears outside the driver;
existing tests and `replay_evidence_corpus.py` pass; Live click telemetry shows
curved, timed moves.

### 2. Skills extraction

Move the skills above out of `BaseTask` one at a time, each with its own tests.
`BaseTask` shrinks to a thin compatibility shell.

Exit: each skill has unit tests with a fake driver; Post and Reel behavior and
evidence replays unchanged.

### 3. Stage lists and outcome

Split Post `run()` into stages, then Reel. One `PublicationStatus`; Finalize is
the only writer. `workflow.yaml` drives the stage order; Live's workflow lists
its real stages.

Exit: `test_publishing_pipeline.py` and `test_fixed_orchestrator.py` cover the
stage lists; no module sets `task.result_status`; an exception after the publish
click still yields `uncertain`.

### 4. Session plan and persona

`SessionPlan`, inline pre-browse and linger, expanded persona traits, active
hours in the scheduler.

Exit: telemetry shows varied session lengths per account within budget; no
browse runs after an uncertain publication; persona is reproducible from the
seed.

### 5. Mouse and typing realism

Fitts timing, overshoot, smooth noise, bounded click points, typing variance and
checked typos.

Exit: input telemetry distributions differ per persona and stay within bounds;
caption verification pass rate does not drop on the evidence corpus.

## Risks

| Risk | Mitigation |
| --- | --- |
| Refactor changes a click and breaks a fragile flow | Milestones 1–3 change structure only; evidence replays gate each step. |
| Longer sessions use more container time and resources | Budgets from the persona, bounded by resource admission. |
| Typos corrupt a caption | Typos only in OCR-verified fields; abort to paste on mismatch. |
| UI navigation fails on an unknown layout | Fall back to typed URL through the humanizer. |
| Persona change alters existing accounts' timing abruptly | Map current presets to trait ranges; bump `BEHAVIOR_VERSION` once. |

## Cloud impact

None to protocol v1. Cloud jobs (`photo`, `reel`) keep the same publish intent,
attempt IDs and uncertain handling. Session plans add local runtime only; the
lease must cover the longer budget, which the job shell checks before
pre-browse.

## Verification

- Fake driver for unit tests of skills, stages and the humanizer.
- `replay_evidence_corpus.py` and `replay_recognition_failures.py` after every
  structural milestone.
- Telemetry comparison per persona before switching defaults.
