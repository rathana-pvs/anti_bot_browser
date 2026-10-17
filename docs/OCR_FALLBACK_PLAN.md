# Shared OCR fallback implementation plan

## Objective

Keep EasyOCR primary and recover bounded text reads with local Tesseract only
when the consuming check cannot use the primary result. Support more than
timestamps: button and input-label searches use the same recovery mechanism.

## Scope and constraints

- Preserve the in-progress author-optional timestamp fixes on this branch.
- No cloud protocol, queue-state, publication-gate, or installed-runtime changes.
- The sibling main-server context files were unavailable at implementation time.
- Never reinterpret `Im` as `1m` in the parser. Independently recognize the pixels.
- Never replace recognized old or conflicting timestamp results with recent ones.
- No unrestricted full-screen secondary OCR or forced character whitelist.

## Implementation

1. Add an optional Tesseract CLI adapter. Send cropped PNGs through stdin, parse
   TSV line bounds/confidence, use bounded subprocess timeouts, and fail closed
   when the executable/model is unavailable. No temporary screenshot files.
2. Add a shared VisionEngine fallback API with a caller-supplied acceptance
   predicate. Primary success bypasses secondary OCR. Secondary results need
   matching normalized text at 3x and 4x, adequate confidence, and overlapping
   geometry. Cache secondary results separately with bounded capacity.
3. Enable fallback for bounded expected-label lookups. Preserve successful
   primary label selection. Secondary labels must match an expected whole label;
   detection alone never bypasses enabled-button or publish-action checks.
4. After existing native/enlarged timestamp reads fail, try the shared fallback
   on tight OCR candidate boxes in the selected first header row. Do not search
   the caption or later posts. Map recovered bounds back to screen coordinates.
5. Record secondary OCR timing/outcome and engine provenance. Add optional
   host/WSL setup dependencies and document configuration/availability.
6. Test bypass, missing binary, timeouts, coordinate mapping, disagreement,
   confidence rejection, label matching, and first-post timestamp constraints.
   Replay profile_002/profile_003 evidence, including old and accepted cases.

## Acceptance

- Prior accepted timestamps and label results remain unchanged.
- Recognized 2h stays rejected as old; old/conflicting readings skip fallback.
- Recover the observed 1m screenshot using automatically derived crop bounds,
   without hard-coded profile IDs, screenshot coordinates, or parser substitution.
- Missing Tesseract preserves the original failure behavior.
- Tests pass; record any evidence recovery limitations honestly.

## Rollout

Repository implementation first. Install/rebuild/redeploy the desktop runtime
separately; do not silently update the installed copy during development.

## Completed validation (2026-10-17)

- Full automation suite: 466 tests and 6 subtests passed.
- `bash -n install.sh` and `git diff --check` passed. Windows/WSL setup
  dependency list updated; the Windows installer was not executed on this host.
- Replayed all seven saved profile-check images for profile_002/profile_003.
  Four now confirm a recent first header: both October 17 `Just now` cases,
  the formerly unreadable October 17 `1m`, and profile_002's October 10
  split/unreadable `Just now` recovered by the bounded retry.
- Three remain unconfirmed: two screenshots do not yield the Posts/List view
  toolbar; one yields incomplete `about ... minute ago` text without a numeric
  age. Do not relax age or first-card constraints to accept these.
- Freshly captured current-post images for both profiles still report 7200
  seconds / `old_top_post`, without invoking Tesseract to override the old age.
- Real saved `Next` and `Post` button crops read correctly through the shared
  secondary API at both scales (minimum confidence approximately 0.96). No
  buttons were clicked during replay.
- Real timestamp recovery used automatically derived OCR bounds. The enlarged
  EasyOCR `Im` box led to independent Tesseract `1m` reads at both scales,
  minimum confidence approximately 0.78, accepted as 60 seconds.
- Tesseract 5.3.4 and English models were unpacked into a temporary test directory;
  no system-wide installation or installed desktop runtime update was performed.

Replay evidence using the automation virtualenv:

```bash
PYTHONPATH=automation python scripts/check_ocr_evidence.py \
  --runtime-root /path/to/installed/runtime \
  --profile profile_002 --profile profile_003
```

Configure `AUTOMATION_OCR_MODEL_DIR`, `AUTOMATION_TESSERACT_BIN`,
`TESSDATA_PREFIX`, and native library paths if using a temporary/custom install.
This command only reads saved screenshots and prints JSON; it does not open
browsers or publish posts. General free-form OCR consumers remain EasyOCR-only
unless they explicitly use the shared fallback API with their own validator.
