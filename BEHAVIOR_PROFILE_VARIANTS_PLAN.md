# Stable Behavior Variants Plan

## Goal

Give each browser profile a stable interaction identity while keeping individual
sessions naturally variable. Profiles will continue using the same automation
workflows and `HumanInput` engine; only the correlated behavior parameters will
change.

This feature must preserve task correctness. Behavioral variation must never
change target selection, workflow decisions, retry policy, safety checks, or
verification logic.

## Design principles

1. **Stable per profile:** a profile receives one archetype and one seed at
   creation. Existing profiles receive them during schema migration.
2. **Variable per session:** each task run derives a fresh session seed and
   applies bounded variation around the profile baseline.
3. **Correlated parameters:** an archetype changes a coherent group of values
   such as movement speed, typing speed, pause length, and scroll cadence.
4. **Local randomness:** the behavior engine owns its own `random.Random`
   instance. It must not seed or mutate Python's global random generator.
5. **Bounded behavior:** every generated value is clamped to reviewed safety and
   usability limits.
6. **Operational waits stay operational:** network, rendering, polling, retry,
   and verification waits are not behavior parameters.

## Initial archetypes

Start with three variants. This is enough to prove the architecture without
creating a large tuning surface.

| Variant | Weight | Interaction character |
| --- | ---: | --- |
| `balanced` | 55% | Moderate mouse, typing, click, and scroll cadence |
| `careful` | 25% | Slower movement and typing, longer settling and reading pauses |
| `quick` | 20% | Faster but still bounded movement, typing, and pauses |

The weights are defaults for new and migrated profiles. Variant selection must
be deterministic from the profile seed so startup order and process restarts do
not change the result.

## Profile data model

Increase `PROFILE_SCHEMA_VERSION` from 4 to 5 and add:

```json
{
  "behavior": {
    "variant": "balanced",
    "profile_seed": 18427,
    "version": 1
  }
}
```

- `variant` is one of `balanced`, `careful`, or `quick`.
- `profile_seed` is generated once using a system source of randomness.
- `version` identifies the behavior preset definitions independently of the
  overall profile schema.
- The initial API treats behavior as system-assigned and read-only. Manual
  selection can be added later if there is a clear product need.

Migration requirements:

- Preserve all existing profile fields.
- Derive the variant deterministically from a newly generated persistent seed.
- Back up the previous config using the existing migration convention.
- Do not mark `restart_required`; behavior is loaded at automation task start
  and does not alter the browser container.
- Make migration idempotent.

## Runtime architecture

Add `automation/engine/behavior_profile.py` with four responsibilities:

1. Define immutable preset ranges for each archetype.
2. Load and validate the profile's behavior block from `config.json`.
3. Derive a `BehaviorSession` from the stable profile seed plus a unique run
   identifier.
4. Provide bounded samplers for mouse, click, typing, keyboard, and scrolling
   parameters.

The intended data flow is:

```text
profiles/<id>/config.json
        -> BehaviorProfile (stable identity)
        -> BehaviorSession (bounded run-level variation)
        -> HumanInput (event-level variation)
```

`BaseTask.__init__` should load the behavior profile before constructing
`HumanInput`, then pass the session object into it. If behavior configuration is
missing or invalid, automation should log a warning and use the `balanced`
fallback rather than fail the task.

Use separate deterministic random streams derived from the session seed for
mouse, typing, click, and scroll behavior. This prevents adding a random call in
one subsystem from unexpectedly changing all later behavior in another.

## Parameter ownership

### Phase 1: central interaction engine

Move the following hard-coded ranges from `automation/engine/human_input.py`
into behavior presets:

- Mouse duration multiplier and minimum/maximum duration
- Mouse path step density
- Bezier deviation and micro-jitter amplitude
- Destination settle pause
- Pre-click pause, button hold, and post-click pause
- Double-click interval
- Baseline typing WPM and per-character cadence spread
- Punctuation pauses
- Hesitation probability and duration
- Scroll notch interval and final pause
- Keyboard post-key pause
- Safe click-point offset may vary only within its existing safe bounds

Keep explicit method arguments authoritative. For example,
`move_to(..., duration_sec=...)` must continue honoring the caller's duration.
Change `type_text` so omitted WPM uses the session baseline, while an explicit
WPM remains a bounded caller override.

### Phase 2: task-level human pauses

After Phase 1 is stable, classify task sleeps into:

- `operational`: page load, polling, retry, upload, publish verification
- `human`: reading, review, hesitation, browsing dwell

Only replace the second category with named behavior-session methods such as
`reading_pause()` and `review_pause()`. Do not multiply arbitrary sleeps.

The first candidates are the reading pauses in `facebook_warming.py`, the
preparation browsing pauses in `facebook_preparation.py`, and the review pause
before publishing in `facebook_post.py`.

## Backend and UI work

Backend changes:

- Extend the Pydantic profile response/types with a behavior block.
- Assign behavior during profile creation.
- Add schema v4-to-v5 migration.
- Include supported variants and weights in `/api/profiles/defaults` for
  observability, even while assignment remains automatic.
- Avoid setting `restart_required` when behavior alone changes.

Manager UI changes:

- Add behavior types to `manager-app/src/types/profile.ts`.
- Display the assigned variant in profile details.
- Do not add an edit control in the first release. Automatic assignment keeps
  the feature simple and prevents accidental identity changes.

## Observability

At task start, record non-sensitive behavior metadata:

- behavior variant
- behavior preset version
- a short hash of the session seed, not the raw seed
- resolved session-level parameter summary

Do not log per-character delays or every mouse point. Aggregate telemetry is
enough to debug the distribution without producing large logs.

## Tests

### Unit tests

- The same profile seed always selects the same archetype.
- Weighted selection maps the full seed range without gaps.
- Two sessions for one profile remain within that archetype's bounds but are
  not identical.
- Different profiles usually produce different baselines.
- Every sampled value respects global hard limits.
- Separate random streams prevent mouse calls from altering typing samples.
- Invalid or missing configuration falls back to `balanced`.
- Explicit `HumanInput` arguments remain authoritative.

### Profile workflow tests

- New profiles persist behavior configuration.
- v4 profiles migrate once to v5 and retain all previous configuration.
- Migration creates the expected backup.
- Listing profiles returns the assigned variant.
- Behavior assignment does not require a container restart.

### Integration tests

- Mock `ContainerClient` and verify movement, clicking, typing, and scrolling
  under all three variants without real sleeping.
- Run identical scripted actions with fixed seeds and compare summarized timing
  distributions.
- Confirm task success/failure and stage transitions do not depend on variant.

## Rollout

1. Land the schema, preset model, and unit tests without changing runtime
   behavior.
2. Wire `HumanInput` to the new session object behind a backend setting,
   defaulting off for one validation cycle.
3. Run deterministic test traces for all three variants.
4. Enable the feature for newly created profiles.
5. Migrate existing profiles and enable after confirming no increase in task
   failures or duration timeouts.
6. Add selected task-level human pauses as a separate change.

Rollback is straightforward: disable behavior sessions so `HumanInput` uses the
balanced preset. Persisted profile behavior data can remain in place.

## Acceptance criteria

- A profile keeps the same assigned archetype after app and machine restarts.
- Repeated sessions from one profile vary but remain within its archetype.
- Interaction traces from the three variants have measurably different timing
  summaries.
- No behavior variant changes workflow decisions or safety checks.
- Existing v4 profiles migrate without data loss.
- Automation falls back safely when behavior configuration cannot be loaded.
- All backend, automation, and manager-app tests pass.

## Recommended implementation order

1. `backend/services/profile_service.py`: schema v5 and deterministic assignment.
2. `backend/models/profile.py` and `backend/routers/profiles.py`: API creation and
   update behavior.
3. `automation/engine/behavior_profile.py`: presets, validation, seed derivation,
   and samplers.
4. `automation/tasks/base_task.py`: load one session per task run.
5. `automation/engine/human_input.py`: consume the session samplers.
6. Backend and automation tests.
7. `manager-app/src/types/profile.ts` and profile-details display.
8. Feature-flagged validation and rollout.

The MVP should stop after Phase 1. It delivers stable profile identities with
low risk; broader workflow pacing can be tuned later using measured results.
