# EasyOCR versus PaddleOCR: local results

Date: 2026-10-18. Branch: `codex/ocr-engine-comparison`.

**Recommendation: use PP-OCRv5 mobile as the first candidate for a future primary
engine trial.** It improved literal recognition and inference speed on this
collection while preserving the tested consumer text decisions. PP-OCRv6 tiny
was fastest, but introduced two timestamp parsing misses on the historical
target crops. The measurements below describe the pre-integration baseline.

**Subsequent source integration:** the manager now offers v5 + Tesseract
(default), v6 + Tesseract (speed), and EasyOCR + Tesseract (legacy/generic).
The saved choice applies after a backend restart. Shared-worker and local-reader
CPU smoke checks passed with all three engines. No installed desktop app was
deployed, and these checks do not establish full publication success.

**Subsequent parser fix:** `about a minute ago` now parses as 60 seconds,
including attributed header rows. The historical misses below were measured
before this fix; their recorded OCR results have not been rerun or relabeled.

## What was tested

- Repository evidence: 664 PNGs from profiles 002/003, 594 unique images after
  exact deduplication. Profile 004 was excluded.
- 120 selected 1920 x 1080 screenshots, balanced by profile/stage, plus 95
  visually reviewed labels on 65 screenshots. Complete runs and conservative
  near-duplicate groups share a split.
- The held-out labels comprise 79 positives and four negative regions; 12 other
  labels belong to the tuning split. No thresholds were tuned.
- Each engine completed 240 full-screen and 190 crop timing samples, plus a
  separate four-client serialized-request simulation. No inference errors in
  the completed comparison runs.
- A separate supplement contains 11 newer installed-runtime screenshots from
  the same profiles. It includes `1m`, `Just now`, `about a minute ago`, and one
  visible `2 days ago` label. That old label belongs to a later post and only
  tests transcription/parsing, not first-post selection.

Hardware: Intel Core i7-11370H, Linux x86_64, Python 3.12, CPU only, four inference
threads. Inputs were capped at 1280 pixels on the longest side; labeled crops
retained native resolution. All 215 historical and 22 supplemental prepared
input fingerprints matched across the isolated environments. Result caches
were disabled. No browsers, account actions, or publication attempts ran.

## Measured inference performance

| Engine | Full screen median / p95 | Crop median / p95 | Peak process RSS |
| --- | ---: | ---: | ---: |
| EasyOCR English | 5,610 / 6,699 ms | 96 / 270 ms | 1,365 MiB |
| PP-OCRv5 mobile + English recognition | 1,280 / 2,237 ms | 51 / 135 ms | 1,315 MiB |
| PP-OCRv6 tiny | 459 / 717 ms | 21 / 53 ms | 1,304 MiB |

V5 was about **4.4x faster on full screens and 1.9x on crops** than EasyOCR.
V6 was about **12.2x faster on full screens and 4.6x on crops**. Process memory
was similar; small model weights did not translate into a large process-memory
reduction in these runs.

Reader initialization took 5.59 s / 1.34 s / 1.39 s respectively, excluding
interpreter startup, common image-library imports, and input validation.
The four-client simulation achieved 5.1 / 21.9 / 43.2 crop requests per second,
with p95 response times of 1,355 / 296 / 152 ms. This is an uncached prepared-crop
simulation, not observed throughput from live concurrent browser profiles.

## Recognition and current text rules

| Measure | EasyOCR | V5 mobile | V6 tiny |
| --- | ---: | ---: | ---: |
| Exact positive crop hits, held out | 69/79 (87.3%) | 74/79 (93.7%) | 72/79 (91.1%) |
| Exact positive full-screen hits, held out | 60/79 | 73/79 | 69/79 |
| Exact button crop hits | 58/62 | 62/62 | 62/62 |
| Negative regions without forbidden text | 4/4 | 4/4 | 4/4 |
| Post-hoc consumer text acceptance | 79/79 | 79/79 | 77/79 |
| Historical timestamp crop parses | 4/4 | 4/4 | 2/4 |
| Supplemental timestamp decisions | 8/11 | 10/11 | 10/11 |

Exact scores normalize case, Unicode, and whitespace, allow line joining, and
score the English `Comment as` anchor independently of account names. At the
existing 0.45 label-confidence cutoff, the crop hit counts above were unchanged.
Full-screen positive hits at that cutoff were 52/79, 73/79, and 69/79.
Character error rates on labeled positive target text were 3.95%, 1.48%, and
2.30%, using the closest transcription inside each reviewed target region.

The consumer diagnostic was added after inspecting exact-match failures. It
uses existing photo/video aliases, the existing description matcher with
required context tokens, and the existing timestamp parser. It restricts
candidates to the reviewed target box. It is diagnostic evidence, **not an
end-to-end automation success rate or proof of click/publication safety**.

Specific findings:

- EasyOCR repeatedly read `Photo/video` as `Photolvideo`. The screen-state
  classifier already recognizes that alias. V5 and v6 read the literal button
  label correctly in these cases.
- EasyOCR and v5 produced describe/pescribe or punctuation variants of the reel
  placeholder. V6 read its words correctly but shortened the ellipsis. The
  current scored description matcher accepted all four variants for all engines.
- V6 appended `g` when reading the privacy icon after `a few seconds ago` in two
  historical crops. The current timestamp parser rejected those readings.
  V5 and EasyOCR preserved usable timestamp parses on these same cases.
- Both Paddle candidates recovered the supplemental `1m` case and one other
  recent timestamp decision missed by EasyOCR at confidence 0.55.
- `about a minute ago` remains unparsed by the existing timestamp parser for
  all engines. This is a separate parser coverage limitation.
- All engines classified the one supplemental old label as old. There are no
  conflicting-age examples here, and one old sample cannot establish a false
  publication rate.

The existing bounded Tesseract recovery implementation was also replayed on
87 eligible historical non-timestamp positive labels with exact labeled
acceptance: EasyOCR alone matched 79/87; recovery raised this to 83/87. This
uses a stricter acceptance predicate than some production consumers and must
not be interpreted as four additional successful automation tasks.

## Runtime compatibility finding

EasyOCR used 1.7.2 with PyTorch 2.14.0+cpu. Completed Paddle runs used PaddleOCR
3.7.0, PaddleX 3.7.2, and **PaddlePaddle 3.2.2 with oneDNN enabled**. Exact model
artifact hashes, official repository revisions, and full environment locks are
preserved with the private results.

The initial PaddlePaddle 3.3.0 accelerated run failed on its first inference:

```text
ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]
```

Disabling oneDNN allowed inference, but an unaccelerated diagnostic was slower
on full screens and was stopped. Those diagnostic reads are excluded from the
completed comparison. Pinning 3.2.2 restored successful accelerated inference.
The [upstream issue](https://github.com/PaddlePaddle/PaddleOCR/issues/18162)
documents this runtime regression and downgrade workaround. A migration must
pin and validate the framework stack; installing an unspecified latest version
is insufficient.

## Limits and next step

Labels were reviewed by the agent from the pixels, without an independent
second annotator. Multiple labels on one screen are correlated; the collection
is historical and stage-balanced rather than a production request sample.
There is no deliberate Facebook light-theme coverage, other resolution, GPU,
other-language, Windows/WSL packaging, cache-hit benchmark, or live profile
concurrency validation. Common-input preprocessing omits VisionEngine's
theme-specific CLAHE. Run order was EasyOCR, v5, v6 on one machine.

Next implementation should add v5 behind an explicit engine option in both
shared and local readers, isolate caches by engine/model/settings, and replay
first-post selection, uncertain-publication safeguards, enabled controls,
fallback behavior, and supported-platform packaging before changing defaults.
Retain v6 as a speed candidate after resolving the observed timestamp behavior
without weakening rejection of old or conflicting ages.

## Artifacts and checks

See [the reproduction guide](OCR_ENGINE_COMPARISON.md) and
[the evaluation plan](OCR_ENGINE_COMPARISON_PLAN.md). Private snapshots,
annotations, raw detections, model provenance, diagnostics, and JSON reports are
under `/home/rathana/.local/share/automat-fb-ocr-evaluation/`, outside Git.

Validation: 12 harness tests passed; dependency checks passed; prepared pixel
hashes matched. Final source-hash verification found all 664 original repository
screenshots unchanged. Original installed-runtime timestamp files were also
verified against their staged copies. No production OCR code, dependency list,
installed app, cloud protocol, or publication state was changed.

## Follow-up: Tesseract timestamp recovery

A separate direct-crop replay used the existing two-scale consensus, geometry
agreement, minimum 0.60 confidence, and timestamp-parser acceptance rules:

- The missed EasyOCR `1m` case recovered using its saved candidate box and the
  same padding as the production timestamp fallback: both scales read `1m`,
  at confidence 0.780 and 0.841. A different manually tightened crop disagreed
  (`1m` versus `Im`) and correctly failed consensus. Crop selection matters.
- The supplemental missed `Just now` case recovered at consensus confidence
  0.771 when its annotated crop was explicitly submitted.
- The two v6 `a few seconds ago G` cases did not recover under the existing
  consensus/confidence rules, either from the annotated crops or from a manual
  diagnostic crop excluding the privacy icon.
- `about a minute ago` did not recover. The current parser also lacks that
  phrase, so successful OCR alone would not resolve its decision.

These are direct recognition probes, not full automatic recovery replays. The
current timestamp hook only submits bounded short alphanumeric candidates
after native and enlarged reads remain unreadable; it does not generally retry
long timestamp phrases. Therefore Tesseract is a useful bounded fallback, but
it does not eliminate the observed v6 timestamp limitation. Raw probe results
remain in the private evaluation directory. No parser or recovery rules changed.

### V5 followed by Tesseract: explicit sequence test

Replayed saved v5 primary results first, submitting failed label reads to the
existing bounded Tesseract consensus implementation:

| Crop-level measure | V5 alone | V5 plus Tesseract |
| --- | ---: | ---: |
| Exact eligible non-timestamp labels, all splits | 83/87 | 87/87 |
| Exact eligible non-timestamp labels, held out | 71/75 | 75/75 |
| Historical timestamp decisions | 4/4 | 4/4 |
| Supplemental timestamp decisions | 10/11 | 10/11 |

Tesseract recovered the four literal reel-placeholder variants. Recovery added
248-277 ms on those failed cases. Existing fuzzy matching already accepts these
variants, so this exact-text gain is not four newly successful application tasks.

Timestamp recovery was attempted only for unreadable primary decisions; old
and conflicting decisions were not retried. V5 already handled all four
historical timestamp crops and ten supplemental cases. Tesseract did not resolve
the remaining `about a minute ago` case. This sequence explicitly submitted the
annotated crop on a miss; it did not replay automatic header discovery, enlarged
primary reads, or complete publication gates. Private sequence artifacts are
`20261018/fallback-paddleocr-v5.json` and `v5-tesseract-timestamps.json` under the
evaluation directory. Production code remains unchanged.

### V5 + Tesseract versus V6 + Tesseract

Both sequences were replayed with the same saved primary inputs, label
confidence cutoff 0.45, and existing bounded Tesseract recovery. Timestamp
decisions used confidence 0.55; secondary reads still required consensus at
3x/4x with confidence at least 0.60. Only unreadable timestamp decisions were
submitted to recovery; old and conflicting decisions were not retried.

| Measure | V5 + Tesseract | V6 tiny + Tesseract |
| --- | ---: | ---: |
| Exact non-timestamp labels, all splits | 87/87 | 87/87 |
| Exact non-timestamp labels, held out | 75/75 | 75/75 |
| Labels recovered by Tesseract | 4 | 4 |
| Historical timestamp decisions | 4/4 | 2/4 |
| Supplemental timestamp decisions | 10/11 | 10/11 |
| Replayed label latency, median | 75 ms | 31 ms |
| Replayed label latency, mean | 92 ms | 47 ms |
| Replayed label latency, p95 | 284 ms | 222 ms |

Latency here is an estimate on the 87-label workload: the saved first primary
inference duration plus newly measured recovery overhead for the same case.
It excludes common image decoding/preparation and is not a newly timed complete
browser or service run. It differs from the earlier medians over all repeated
crop reads. Mean additional recovery time on the four failed label cases was
281 ms for v5 and 267 ms for v6; successful primary reads bypassed Tesseract.

Tesseract recovered the four literal reel-placeholder variants for both
engines. Existing fuzzy matching already accepts those variants. It did not
repair v6's two historical timestamp misses, and neither combination resolved
the supplemental `about a minute ago` case. Both preserved rejection of the
single old timestamp example; conflicting-age coverage remains absent.

The recommendation therefore remains **v5 + bounded Tesseract for the first
primary-engine trial**. V6 + Tesseract is faster, but the backup does not remove
its observed timestamp parsing limitation. These are annotated-crop sequence
tests, not automatic first-header discovery or full action/publication gates.
Private paired results are saved as `v5-v6-tesseract-comparison.json` under the
evaluation directory. No recovery thresholds, parser rules, or production
engine defaults were changed.

### EasyOCR + Tesseract in the same sequence comparison

The same saved-primary/annotated-crop replay was also run for EasyOCR, using
identical confidence and recovery rules:

| Measure | EasyOCR + Tesseract | V5 + Tesseract | V6 tiny + Tesseract |
| --- | ---: | ---: | ---: |
| Exact non-timestamp labels, all splits | 83/87 | 87/87 | 87/87 |
| Exact non-timestamp labels, held out | 71/75 | 75/75 | 75/75 |
| Historical timestamp decisions | 4/4 | 4/4 | 2/4 |
| Supplemental timestamp decisions, same annotated crops | 9/11 | 10/11 | 10/11 |
| Replayed label latency, median | 106 ms | 75 ms | 31 ms |
| Replayed label latency, mean | 150 ms | 92 ms | 47 ms |
| Replayed label latency, p95 | 424 ms | 284 ms | 222 ms |

Latency uses saved first-primary inference durations plus freshly measured
recovery overhead. It excludes decoding/preparation and does not replace a
complete service or browser workflow benchmark. EasyOCR attempted label
recovery eight times and recovered four cases; the Paddle combinations each
attempted four and recovered four. Mean extra recovery time for an attempted
EasyOCR label miss was 260 ms.

The four remaining exact EasyOCR label misses were `Photo/video` variations
already recognized by the current screen-state aliases. They are not evidence
of four failed production tasks. Its supplemental timestamp count improved
from 8/11 to 9/11 by recovering `Just now`. The unchanged annotated `1m` crop
still failed consensus, but the earlier separate probe using the saved EasyOCR
candidate box and production padding recovered `1m` at both scales. Thus the
9/11 figure is specifically the same-crop comparison, not a finding that the
existing automatic tight-crop recovery cannot handle `1m`.

EasyOCR + Tesseract remains a viable baseline. The main case for a v5 trial is
faster inference with fewer literal recognition errors; full consumer/gate
validation is still required. Private sequence data is saved as
`easyocr-tesseract-comparison.json` in the evaluation directory.
