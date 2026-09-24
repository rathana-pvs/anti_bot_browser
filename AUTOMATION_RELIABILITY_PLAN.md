# Visual Posting Reliability Plan

## Objective

Make queue results describe what was visually observed, rather than whether the
Python process merely reached its final line. The automation must never repeat a
final publish action when the result is ambiguous.

## State model

The photo workflow uses these observable states:

1. `login_required`
2. `feed_ready`
3. `composer_open`
4. `media_uploading`
5. `post_enabled`
6. `publishing`
7. `post_confirmed`
8. `error_dialog`
9. `unknown`

OpenCV supplies template, color, and geometry signals. EasyOCR supplies visible
text. A transition is allowed only when the expected signals are present.

## Outcome contract

- `published`: a success message was observed, or the composer closed and the
  changed screen returned to a stable feed state twice.
- `failed`: a failure occurred before the publish action, or Facebook displayed
  an explicit error afterward.
- `uncertain`: the final publish action was sent once but neither success nor an
  explicit failure could be confirmed. These jobs require manual review and are
  never retried automatically.

## Implemented foundation

- [x] Lazy, pretrained EasyOCR integration with project-local model storage.
- [x] OpenCV template and enabled-blue-action signals.
- [x] Structural similarity checks for before/after screens.
- [x] Screen-state recognizer with prioritized login, error, and success states.
- [x] Stable target requirement across consecutive observations.
- [x] No fixed-coordinate fallback for the final publish path.
- [x] Evidence directory containing screenshots, metadata, and final result.
- [x] Queue support for `uncertain` and evidence-directory metadata.
- [x] Guarded Reel upload, Next, Publish, and confirmation workflow.
- [x] Offline tests for the core recognition rules.

## Next calibration phase

1. Run a supervised photo post on a non-critical test Page/profile.
2. Review screenshots under `profiles/<id>/automation_evidence/`.
3. Add or replace templates for the exact Facebook theme and resolution.
4. Tune OCR and template confidence using at least 20 successful and failed
   screenshots.
5. Add caption/media fingerprint verification for the newly created feed item.
6. Calibrate the Reel workflow against supervised test uploads.

## Acceptance criteria

- A missing or disabled Post button never receives a click.
- A failed file chooser never leads to typing into the browser.
- A process exit code alone never converts an ambiguous photo job to published.
- Every failed or uncertain execution has a final screenshot and error code.
- Restart recovery does not redispatch an `uncertain` execution.
- The full offline suite and manager application build pass.
