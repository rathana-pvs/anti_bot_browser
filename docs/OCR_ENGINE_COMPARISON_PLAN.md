# EasyOCR and PaddleOCR evaluation plan

Created: 2026-10-18
Branch: `codex/ocr-engine-comparison`
Status: local evaluation completed. See [measured results](OCR_ENGINE_COMPARISON_RESULTS.md).

Source integration now exposes v5 + Tesseract (default), v6 + Tesseract (speed),
and EasyOCR + Tesseract (legacy/generic) in the manager. Selection persists and
applies after a backend restart. The evaluation baseline below describes the
pre-integration code. No installed desktop app was deployed by this change.
Production migration remains a separate follow-up.

## Objective

Determine whether PaddleOCR should replace EasyOCR as the primary reader for
Facebook visual automation. Base the decision on saved local evidence, correct
action targets and timestamp decisions, latency, and resource use. Keep EasyOCR
primary while evaluating.

## Scope and constraints

- Use evidence from `profile_002` and `profile_003`; exclude `profile_004`.
- Run offline: no browser launches, account actions, or publication attempts.
- Read existing evidence without changing it. Store dataset copies and results
  outside live profile directories and evidence-cleanup paths.
- Keep screenshots, annotations, and OCR output local and out of Git. Never copy
  cookies, sessions, or browser profile directories into the dataset.
- Use an isolated evaluation environment; do not change the installed desktop
  runtime or production dependencies for an experiment.
- Preserve uncertain-publication handling, timestamp rules, resource admission,
  and action/geometry checks. OCR output alone never authorizes a click.
- No cloud contract or shared-state changes are needed for this offline work.
  The sibling `../main-server` repository and its required context files are
  unavailable in this checkout. Revisit that context before any later work
  affecting integration.

## Current baseline

The repository evidence inventory observed on 2026-10-18 is:

| Profile | PNG screenshots | Resolution | Approximate size |
| --- | ---: | --- | ---: |
| `profile_002` | 201 | 1920 x 1080 | 106 MiB |
| `profile_003` | 463 | 1920 x 1080 | 298 MiB |
| Total | 664 | | 404 MiB |

These historical screenshots span 2026-09-24 through 2026-10-01 and include
posting, reels, comments, preparation, warming, dialogs, and failures. Repository
and installed-runtime evidence are separate collections: inventory each
explicitly if the latter is used, then deduplicate across sources. The 664 count
above applies only to the repository collection and is not a unique-image count.

Existing metadata and telemetry help select samples but are not ground truth.
Human review must establish expected visible text, valid target geometry, and
timestamp decisions independently of previous OCR output and task status.

Relevant implementation:

- `automation/ocr_worker.py`: shared warm English EasyOCR reader, serialized
  inference, and image cache.
- `automation/engine/vision.py`: local reader, preprocessing, region mapping,
  label matching, confidence filtering, and bounded secondary reads.
- `automation/engine/tesseract_ocr.py`: optional Tesseract recovery.
- `scripts/check_ocr_evidence.py`: saved first-post screenshot replay.
- `automation/benchmark_ocr_threads.py`: existing EasyOCR timing harness.
- `backend/services/evidence_cleanup.py`: evidence retention behavior.

## Phase 1: preserve and inventory evidence

1. Add an offline inventory tool with explicit source roots and a separate
   output directory. Allow only the two selected profile evidence directories.
2. Validate image decoding; record relative source path, profile, run, stage,
   dimensions, byte size, and SHA-256. Report missing or unreadable files.
3. Make a local snapshot of selected evidence before normal retention can
   remove it. Copy screenshots and necessary diagnostic metadata only.
4. Deduplicate exact matches and group visually similar frames. Retain source
   provenance; do not silently remove distinct timestamps or target states.
5. Produce a machine-readable manifest with a dataset version and hashes.

Deliverable: reproducible local dataset inventory and immutable input snapshot.

## Phase 2: curate and label

1. Select approximately 100-200 distinct screenshots, adjusted to actual
   coverage. Include successes and failures, small gray text, dialogs, buttons,
   comment inputs, publication checks, and all available timestamp cases.
2. Record theme and UI language from inspection. Report missing coverage rather
   than claiming this collection covers all themes, languages, or resolutions.
3. Label task-relevant regions with expected text, target boxes, accepted label
   variants, and whether a valid action target is absent. Mark ambiguous or
   unreadable regions explicitly rather than guessing.
4. Label timestamp text and expected fresh/old/unreadable/conflicting decisions
   using the existing application rules. Include negative examples where found;
   record gaps that need later evidence collection.
5. Split tuning and held-out evaluation by run and near-duplicate group, with
   both profiles represented where feasible. Freeze the split and labels before
   tuning model settings. Keep repeated frames out of opposite splits.

Deliverable: reviewed annotations, coverage summary, and fixed split manifest.

## Phase 3: build isolated engine runners

1. Reproduce the current EasyOCR English model and preprocessing as baseline.
   Record installed package versions, model hashes, thread count, and device.
2. Verify currently available PaddleOCR releases and supported CPU/GPU setups
   from official documentation. Pin the selected package versions and model
   artifacts; do not benchmark an unspecified default model.
3. Evaluate a lightweight English-capable PP-OCR candidate first. Consider
   PP-OCRv6 if supported by the evaluation environment; test an additional model
   only when accuracy or resource results justify it.
4. Normalize both engines to text, polygon/box in original image coordinates,
   confidence, engine/model identity, and inference timing. Preserve raw output
   for debugging. Do not treat confidence scores as interchangeable.
5. Use identical inputs and a documented common preprocessing pass for the
   direct comparison. Separately report any engine-specific optimized pipeline.
   Disable document orientation/unwarping features where unnecessary for UI.
6. Run engines in separate processes/environments to avoid dependency and device
   memory interference. Download pinned models during setup, then run offline.

Deliverable: repeatable comparison CLI without production engine changes.

## Phase 4: measure accuracy and performance

1. Measure label precision/recall and false matches on absent-target samples;
   inspect whether the selected click point lies inside the labeled target.
2. Measure exact timestamp reads and final timestamp decisions, emphasizing
   any old/unreadable/conflicting case incorrectly accepted as fresh.
3. Report character/word error rates on labeled regions as supporting metrics,
   alongside application-level outcomes and categorized failure examples.
4. Tune confidence thresholds only on the tuning split; freeze them before
   held-out scoring. Report per-category counts and paired differences, not
   just a single overall accuracy percentage.
5. Measure cold initialization separately from warmed inference, median/p95
   latency, peak process memory, and GPU memory where available. Record hardware,
   thread settings, repeats, and warm-up procedure.
6. Disable caches for direct inference timings. Measure cache hits separately;
   use the same workload and cache policy when comparing pipeline throughput.
7. Simulate requests from multiple profiles against each candidate's serialized
   shared-service behavior. Report queue waiting, inference time, end-to-end
   response time, and throughput separately. No live browsers are needed.
8. Compare primary engines alone first. Separately replay the current
   EasyOCR-plus-Tesseract baseline so secondary recovery does not obscure the
   practical effect of an engine change.

Deliverable: local detailed results and a concise report with failures, resource
costs, coverage limits, and a recommendation. Commit only a sanitized summary.

## Phase 5: decision and possible follow-up

Promote a candidate only when held-out evidence shows:

- No observed increase in false action matches or unsafe fresh-timestamp
  acceptance; manually inspect every such discrepancy. Zero observed failures
  in a small dataset is not proof of zero production risk.
- Useful improvements in task-level recognition or latency, with resource use
  compatible with concurrent local profiles.
- Reproducible setup and offline startup on the supported deployment targets;
  distinguish tested platforms from those still unverified.

Agree numeric improvement targets and resource budgets after collecting the
baseline, before interpreting held-out results. If results are inconclusive,
retain EasyOCR and document the evidence needed to decide.

A production migration is a separate follow-up after the comparison: add a
selectable engine through shared and local execution paths, isolate caches by
engine/model/settings, recalibrate thresholds, retain rollback, and validate
packaging and bounded secondary recovery. Do not switch the default merely
because an adapter exists. Installed-runtime deployment is outside this plan.

## Verification and completion checklist

- [x] Source evidence remains unchanged; snapshot and manifest hashes match.
- [x] Profiles 002/003 included and profile 004 excluded.
- [x] Labels reviewed and run/exact/conservative near-duplicate groups kept together.
- [x] Meaningful harness checks cover coordinate mapping, malformed/missing
      inputs, engine failures, and consistent metric calculations.
- [x] Pinned engines evaluated on the same held-out inputs and hardware.
- [x] Uncached inference, startup, and queued-request timings distinguished.
- [x] Current Tesseract recovery baseline reported separately with its predicate limits.
- [x] Recommendation includes concrete numbers and known dataset limitations.
- [x] Production OCR remains unchanged during evaluation.

## Execution record (2026-10-18)

Implemented the inventory/snapshot, isolated runners, fingerprint verification,
scoring/reporting, bounded recovery replay, and timestamp/consumer diagnostics
under `scripts/ocr_eval/`. Evaluated 120 historical screens and 95 reviewed
labels, plus 11 newer timestamp screenshots from profiles 002/003. No thresholds
were tuned and no numeric release budget was declared retrospectively.

V5 mobile is the recommended first primary-engine trial candidate. V6 tiny was
fastest, but two historical timestamp reads failed current consumer parsing.
The accelerated PaddlePaddle 3.3.0 path failed; completed comparisons use the
documented 3.2.2 compatibility pin. Twelve harness tests passed.

The evaluation does not complete production migration or release qualification.
Independent annotation review, broad negative/conflicting timestamp coverage,
cache-hit behavior, complete action gates, installed-runtime deployment, and
supported-platform packaging remain follow-up validation. Per-engine tuning,
GPU testing, and any additional model comparisons were not needed for this
initial CPU recommendation.
