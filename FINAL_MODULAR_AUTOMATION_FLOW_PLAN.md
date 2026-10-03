# Final Modular Automation Flow Plan

Updated: 2026-10-03. Status: baseline implementation complete; live fixture
capture and canary rollout remain operational follow-up work.

This version supersedes the earlier shared Post branching and strict Reel
assignment design. Post uses separate templates selected automatically. Reel
supports Auto or direct manual selection.

Implemented on `codex/modular-automation-flow`:

- fixed Startup/Warming/Publish/Prompt/Comment/Finalize orchestration;
- profile behavior modes and Auto/T1/T2/T3 Reel assignment;
- validated P1/P2 and T1/T2/T3 template registry, detector, and safe executor;
- Post automatic routing and Reel automatic/manual routing;
- shared optional-prompt handling and engine-owned Post/Reel publish gates;
- independently reported comment outcomes and one final evidence/telemetry write;
- pinned Post and Reel Brain packages with version and content digest metadata.

Real Facebook screenshot fixtures and shadow/canary metrics cannot be produced
from unit tests alone. Capture them from authorized profile runs before declaring
new composer variants production-ready.

## 1. Fixed Pipeline

```text
Startup
  -> Warming (skip when disabled)
  -> Publish
       Text/Image -> detector -> P1/P2/future Post template
       Reel
         Auto     -> detector -> T1/T2/T3/future Reel template
         Assigned -> execute selected Reel template directly
  -> Optional post-publish prompt
  -> Comment (skip when no comment supplied)
  -> Finalize
```

Modules execute sequentially against one browser. Stopping a module stops further
browser actions; Finalize still runs to record the outcome. Standalone warming,
preparation, and comment commands may reuse components without publishing content.

## 2. Profile Settings

Expose both settings in Create Profile and Edit Profile:

| Setting | Options | Default |
| --- | --- | --- |
| Behavior mode | Fast, Medium, Slow | Medium |
| Reel template | Auto, T1, T2, T3, future registered templates | Auto |

Proposed fields, merged into the existing profile document:

```json
{
  "behavior_mode": "medium",
  "automation": {
    "reel_template": "auto"
  }
}
```

- Text/image posts always use automatic template selection.
- Auto is a selection policy, not a template ID.
- Manual Reel selection skips template detection completely.
- Normal step checks still verify required controls and expected transitions.
- A failed manual-template step stops execution; it never silently switches templates.
- Changes affect subsequent jobs without a browser restart.
- Snapshot settings, behavior definitions, and Brain version/digest for each job.

Use the next available profile schema version when implementing. Missing settings
default to Medium and Auto. Preserve all existing fields, use atomic writes and
migration backups, and keep migration idempotent. Reject unknown explicit template
IDs; do not accept paths as IDs.

The older behavior plan's automatic, read-only assignment is superseded by
user-selectable modes. If legacy variants exist, map quick -> fast,
balanced -> medium, and careful -> slow.

## 3. Module Responsibilities

| Component | Responsibility |
| --- | --- |
| Startup | Confirm container/login; detect theme and environment |
| Warming | Optional bounded scrolling and pauses; finish at a known screen |
| Publish | Route content to Post or Reel publisher |
| Optional prompt handler | Dismiss the known post-publish popup if present |
| Comment | Choose an applicable method for the intended published post |
| Finalize | Save outcomes, evidence, and telemetry on every exit |

Startup performs common checks once. Wrapped tasks must not repeat startup,
comment submission, or finalization when the orchestrator owns those steps.
Optional environment observations may return unknown; required login checks must
succeed before publication.

## 4. Initial Composer Templates

Extract these families from the existing runtime code:

| ID | Family | Composer sequence |
| --- | --- | --- |
| P1 | Post direct | Composer -> Post |
| P2 | Post review | Composer -> Next -> Review -> Post |
| T1 | Reel Studio | Upload -> optional Next/Edit -> final composer -> Post |
| T2 | Reel direct | Direct chooser -> final composer -> Post |
| T3 | Reel Next/Share | Direct chooser -> editing screen -> Next -> Share review -> Post |

These are proposed stable IDs. Confirm their exact mapping with screenshot
fixtures before exposing assignments. Optional stages within a family do not
automatically require additional templates.

For initial Post P1/P2, share composer opening, optional image attachment, caption
entry, and readiness checks. Then detect the template for the final-screen
sequence. Reel templates own their different upload/editing sequences.

If a future Post composer changes opening or caption entry, move detection to
that earlier boundary. Do not assume every future variant can pass the current
shared preparation steps.

## 5. Generic Detector and Executor

Each template declares:

- ID, content family, and supported environment conditions;
- recognition rules with required positive and disqualifying signals;
- labels, assets, regions, and spatial relationships;
- steps, preconditions, expected transitions, and bounded waits;
- final publish request and verification requirements;
- positive, negative, and transition screenshot fixtures.

For automatic selection:

1. Capture observations once and share OCR/visual results across candidates.
2. Evaluate eligible templates using their declared recognition rules.
3. Require positive evidence, a minimum score, and a clear lead over alternatives.
4. Confirm a stable choice across observations within a bounded deadline.
5. Return the selected template and evidence, or needs_review if no clear match exists.

Missing OCR text alone must not prove a different layout. Calibrate scores and
thresholds with fixtures; earlier illustrative numbers are not production defaults.

Manual Reel selection loads and executes the chosen template directly. It does
not call recognition scoring or the generic detector. Required step checks still
apply before clicking or typing.

The detector has no P1/P2/T1-specific branches. Adding a composer normally means
adding a template, recognition rules, a registry entry, and fixtures. A genuinely
new interaction capability can still require an engine update.

The executor accepts validated declarative data and calls allow-listed engine
capabilities. Define and validate conditions/transition syntax; do not execute
arbitrary Python, shell, or expressions from templates.

## 6. Publication Boundary

Both publishers use the engine-owned final publish gate. At most one final
publish attempt is allowed per job. The existing Post task uses this gate; the
current Reel task directly clicks the final action. Wiring Reel through a gate
that supports its verified publication states is required implementation work.

Publication must be verified independently. Popup dismissal or a visible feed
alone does not prove that the intended item was published. Preserve uncertainty
across failures, process restarts, and retries.

## 7. Optional Post-Publish Popup

One shared handler serves Post and Reel with two normal branches:

```text
Known popup appears
  -> click its modal-scoped "Not now"
  -> wait until it disappears
  -> continue

No popup appears within the bounded observation window
  -> continue
```

Recognize the popup and its button together. Failed dismissal stops progression
to Comment and records the error without erasing confirmed publication.

The handler may run during publication verification if the popup blocks the
screen. Complete it before commenting, sharing state to avoid duplicate clicks
or another full wait after a prior dismissal. Popup absence/dismissal does not
create publication success.

Preserve the existing supported Reel remix/original-audio step within its Reel
publication flow. It is separate from the optional Not now handler.

## 8. Comment Module

Empty comment_text returns skipped/comment_not_provided.

When supplied, choose an applicable method in this order:

1. A verified comment input belonging to the intended post already on screen.
2. A verified permalink for that post.
3. Locate the published post using correlation evidence.

A visible input alone is insufficient without post identity evidence. Try another
method only before submission and when the current state permits it. Once a
comment may have been submitted, uncertainty stops further attempts.

Store publication and comment statuses separately. A comment failure must never
cause a confirmed publication to be retried.

## 9. Behavior Modes

Keep Fast, Medium, and Slow definitions in one central configuration. One
BehaviorSession per job supplies bounded movement, typing, scrolling, reading,
and review pauses, plus warming intensity.

Use local seeded randomness and reproducible run metadata. Variation may change
pauses and bounded warming activities; it must not randomly omit requested posts
or comments, or select weaker targets.

Upload, network, polling, and verification waits remain operational timeouts.
Modes never change recognition thresholds, publish gates, retry semantics, or
verification requirements. Reuse applicable sampler guidance from
BEHAVIOR_PROFILE_VARIANTS_PLAN.md with the selectable modes defined here.

## 10. Context and Results

One run context carries job/profile identity, inputs, snapshotted settings,
environment observations, behavior session, namespaced module outputs,
publication-attempt state, and evidence references.

Modules return structured results rather than invoking the next module:

```python
@dataclass
class ModuleResult:
    outcome: str
    reason: str = ""
    outputs: dict = field(default_factory=dict)
```

| Outcome | Meaning |
| --- | --- |
| success | Module completed its verified responsibility |
| skipped | Optional work was not requested |
| failed_safe | Module failed before its irreversible action |
| uncertain | Its irreversible action may already have occurred |
| needs_review | Execution cannot continue confidently |

Record skipped modules explicitly. Failures stop subsequent browser actions.
Invoke Finalize through a finally boundary; preserve the original error if
finalization also fails. Finalize performs no browser interaction.

Map outcomes explicitly to the existing queue/API contract. Preserve confirmed
publication even when Comment fails or is uncertain. Finalization aggregates
results without making the publishing job retryable again.

## 11. Proposed Organization

```text
automation/
  engine/
    automation_context.py
    module_contract.py
    fixed_orchestrator.py
    behavior_profile.py
    post_publish_prompt.py
    brain_runtime.py
  modules/
    publishing_pipeline.py
  composer_templates/
    registry.py
    detector.py
    executor.py
  brains/
    facebook_post/bundled_default/
      manifest.json
      templates/p1.yaml
      templates/p2.yaml
      config/
      tests/
    facebook_reel/bundled_default/
      manifest.json
      templates/t1.yaml
      templates/t2.yaml
      templates/t3.yaml
      config/
      tests/
```

Reuse existing tasks/helpers incrementally. Extend the current Brain loader and
versioned schema deliberately; its existing workflow format does not implement
this registry, detector, or template executor.

No generic graph engine, executable module installation, or arbitrary pipeline
reordering is required.

## 12. Implementation Order

1. Contracts and orchestration: add context/results and fixed ordering; guarantee
   finalization on every exit and preserve queue status semantics.
2. Shared modules: extract Startup, Warming, Comment, and Finalize; remove duplicate
   responsibilities from publishers.
3. Optional popup: consolidate Post/Reel handling, including checks during
   verification and before Comment.
4. Behavior/settings: add central modes, migration, backend validation, and
   Create/Edit selectors. Expose only implemented registered templates.
5. Template extraction: capture fixtures and extract Post P1/P2 and Reel T1/T2/T3,
   preserving established transitions and deadlines.
6. Detector/executor: implement validated rules, shared observations, ambiguity
   handling, step execution, and engine publication gates.
7. Routing: Post always detects; Reel Auto detects; manual Reel bypasses detection.
   Pin template package versions per job.
8. Validation/rollout: compare decisions in observation-only shadow runs, canary
   new execution paths, and remove superseded branches after parity.

## 13. Tests and Acceptance Criteria

- The fixed order holds; Finalize runs after success, exceptions, and early stops.
- Disabled warming and absent comments return recorded skipped outcomes.
- Post P1/P2 detection works with both text and image paths.
- Reel Auto detects T1/T2/T3; manual selection never invokes the detector.
- Wrong manual assignments fail at execution checks without silently switching
  templates or issuing an unsafe publish action.
- Unknown/ambiguous automatic matches stop; transient misses remain bounded.
- Fixtures cover supported themes/viewports, positive/negative recognition,
  transitions, and optional popup present/absent.
- Both publishers use the publish gate; uncertain publication cannot retry.
- Comment methods verify post identity and cannot submit twice through fallback.
- Confirmed publication survives comment failure.
- Behavior modes change pacing while preserving functional decisions.
- Migration preserves data and is idempotent; setting edits affect subsequent jobs.
- Existing queue, profile, evidence, Brain installation, and support-bundle
  contracts remain compatible or receive tested explicit migrations.

## 14. Observability and Rollout

Record flow version, mode/version, selection policy, selected template/package
digest, automatic recognition evidence where applicable, module durations,
publication/comment outcomes, and the normal prompt result: dismissed or absent.
Manual runs record that detection was bypassed. Avoid logging raw caption/comment
contents or per-character timing.

Roll out by content type/template using completion, uncertainty, and duration
metrics. Rollback selects the previous compatible execution path/package for
subsequent jobs. It must never retry an already-attempted publication or override
a running job's pinned configuration.
