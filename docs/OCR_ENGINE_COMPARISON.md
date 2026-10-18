# Offline OCR comparison

The evaluation tools live in `scripts/ocr_eval/`. They do not change the
configured runtime OCR worker or launch browsers. See `OCR_ENGINE_COMPARISON_PLAN.md`
for the evaluation scope and promotion criteria.

## Setup

Run commands from the repository root. Use the existing automation environment
for EasyOCR, with its local CRAFT/English model files. Create a separate PaddleOCR
environment; the requirements below are pinned to the evaluated CPU stack.

```bash
python3 -m venv .venv-ocr-eval
.venv-ocr-eval/bin/pip install -r scripts/ocr_eval/requirements-paddle.txt
python3 scripts/ocr_eval/download_models.py \
  --output /home/rathana/.local/share/automat-fb-ocr-evaluation/models
```

Model setup is the only step that needs model-network access. Downloads resolve
an official PaddlePaddle repository revision once and record it with file
hashes. Existing model directories are never overwritten. Runtime readers use
explicit local directories. Keep environment lockfiles alongside local results
when reproducing a run on another machine.

## Preserve and select evidence

```bash
automation/venv/bin/python scripts/ocr_eval/compare.py inventory \
  --source /home/rathana/Projects/automat_fb \
  --output /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018 \
  --limit 120 --seed 20261018
```

Use a fresh output directory outside every source root. The inventory copies
only PNG evidence from profiles 002/003, validates decoding and SHA-256, and
preserves source aliases for exact duplicates. It never copies browser data.
Select additional source roots explicitly if desired. The selection balances
profile/stage buckets with a fixed seed; it is not a random sample of all
production requests.

The manifest groups complete runs and exact/near duplicates before assigning
tuning/held-out splits. Near duplicates use a conservative 1024-bit difference
hash with Hamming distance at most two. They stay in the snapshot; grouping
does not establish equivalent text. Automated perceptual grouping cannot
guarantee the absence of all visually similar leakage. Inspect groups before
claiming generalization.

Fill the generated `annotations.json` through visual inspection before running
either engine. Each label has this shape (coordinates use original pixels,
with right/bottom endpoints exclusive for crops):

```json
{
  "label_id": "label_001",
  "id": "image ID from manifest",
  "region": [900, 700, 1150, 800],
  "target_box": [990, 725, 1060, 765],
  "expected": ["Next"],
  "category": "button",
  "absent": false
}
```

`expected` lists exact accepted transcriptions, case-folded with whitespace and
Unicode normalization. An `absent` label defines forbidden text in a reviewed
negative region. Do not infer expected text from the previous engine's output.
Label ambiguous regions separately or exclude them with a recorded reason.
Metadata target coordinates are navigation hints, not independent labels.

Annotations bind to the manifest hash; results bind to both manifest and
annotation hashes. Changes require fresh runs. Private annotations, screenshots,
raw OCR, contact sheets, and full environment locks stay outside Git.

## Run engines sequentially

```bash
automation/venv/bin/python scripts/ocr_eval/compare.py run \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018 \
  --engine easyocr --threads 4 --repeats 2

.venv-ocr-eval/bin/python scripts/ocr_eval/compare.py run \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018 \
  --engine paddleocr --name paddleocr-v5 --threads 4 --repeats 2 \
  --detection-dir /home/rathana/.local/share/automat-fb-ocr-evaluation/models/PP-OCRv5_mobile_det \
  --recognition-dir /home/rathana/.local/share/automat-fb-ocr-evaluation/models/en_PP-OCRv5_mobile_rec

.venv-ocr-eval/bin/python scripts/ocr_eval/compare.py run \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018 \
  --engine paddleocr --name paddleocr-v6 --threads 4 --repeats 2 \
  --detection-model PP-OCRv6_tiny_det --recognition-model PP-OCRv6_tiny_rec \
  --detection-dir /home/rathana/.local/share/automat-fb-ocr-evaluation/models/PP-OCRv6_tiny_det \
  --recognition-dir /home/rathana/.local/share/automat-fb-ocr-evaluation/models/PP-OCRv6_tiny_rec

python3 scripts/ocr_eval/compare.py summarize \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018 \
  --runs easyocr paddleocr-v5 paddleocr-v6
```

Both engines run CPU-only with the same thread budget, common input preparation,
1280-pixel maximum dimension, and no result cache. No upscaling or dark-theme
CLAHE is applied. Consequently, these common-input results are not a full
reproduction of VisionEngine's theme-dependent targeted-read pipeline. Paddle
document orientation, unwarping, and line orientation are disabled. Paddle
MKL-DNN is enabled using the pinned PaddlePaddle 3.2.2 compatibility runtime.
The initial 3.3.0 runtime failed at first inference with
`ConvertPirAttribute2RuntimeAttribute` when acceleration was enabled. An
unaccelerated diagnostic was stopped after observing slower full-screen reads;
it is not included in the completed benchmark comparison. The failure and
runtime downgrade workaround are documented in the
[upstream issue tracker](https://github.com/PaddlePaddle/PaddleOCR/issues/18162).
`--no-mkldnn` remains available for separately named compatibility diagnostics.
Timing includes each engine's
detector, recognizer, and library processing, but excludes common decoding and
resizing. Queue timings are a separate four-client simulation over twelve
prepared crops with serialized inference; no live profiles are involved.

Initialization and the first warm-up are separate from steady-state timings.
Reader initialization excludes interpreter startup, common image-library
imports, and dataset validation. OS file caches are not flushed. The initial
experiment runs EasyOCR, then PaddleOCR v5, then v6 on one machine; timings are
not a cross-hardware or randomized-order performance claim.
Linux peak RSS covers the process and imports, not just model weights. No GPU
claims can be made from these CPU runs. Sample errors are preserved and count
as failed recognition, not successful negative cases. Partial files aid failure
diagnosis; completed result files cannot be silently overwritten.

The summary reports exact target-region candidate hits and supporting CER/WER
on positive labels, with negative-region false text matches separately. It
allows line joining and uses the closest transcription within the labeled
geometry for edit distance. This is a transcription/geometry benchmark, not
production click-selection precision. Full-screen target hits are reported
separately from targeted crops. Repeated timing reads do not multiply the
accuracy denominator. Multiple labels on one screen are correlated observations.
Comment input labels score the English `Comment as` anchor independently of
the dynamic account-name suffix, matching the application's label semantics.

No confidence tuning is performed by default (threshold zero). Engine
confidence scores are not calibrated to one another. Freeze any thresholds on
the tuning split before using held-out results for promotion decisions.

To measure current bounded Tesseract label recovery from saved primary crops:

```bash
automation/venv/bin/python scripts/ocr_eval/compare.py fallback \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018
```

This reuses VisionEngine's two-scale agreement and confidence checks with an
exact labeled-target acceptance predicate. It skips timestamp and negative
cases; it is not a replay of all production fuzzy matching, theme enhancement,
or publication gates. The default primary threshold is 0.45 and the existing
secondary minimum remains 0.60. Missing Tesseract stays unavailable, not a
successful recovery. Recovery timing is additional to saved primary latency.

## Supplemental timestamp checks

Keep newer installed-runtime screenshots in a separately versioned snapshot
so historical labels and splits remain frozen. Review the exact timestamp
pixels and record `expected_recent` on timestamp annotations. A timestamp from
a later visible post can test transcription, but cannot validate first-post
selection. Record that distinction with the annotation.

Run the same engines against this supplemental dataset with `--crops-only`,
then evaluate the existing timestamp parser over their saved detections:

```bash
automation/venv/bin/python scripts/ocr_eval/compare.py timestamps \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/timestamps
```

This reports recent/old/unreadable/conflicting crop parses at confidence 0.55.
It does not execute a browser, select a post, or authorize publication. An
unreadable result counts as a missed decision, even for an old timestamp; it
does not count as correct recognition merely because it fails closed.

## Diagnose current consumer text rules

```bash
python3 scripts/ocr_eval/compare.py consumers \
  --dataset /home/rathana/.local/share/automat-fb-ocr-evaluation/20261018
```

This is a post-hoc diagnostic alongside the frozen exact-text metric. It uses
the existing description matcher (0.78, required `your`/`reel` tokens), reads the
photo/video aliases from the current screen-state source, and calls the existing
timestamp parser. Confidence cutoffs are 0.18 for the description placeholder,
0.55 for timestamps, and 0.45 for the other labels. Other expected labels use
the existing substring-style text lookup rule. The diagnostic still restricts
candidates to reviewed target boxes; it is not a replay of full module logic,
enabled controls, first-post selection, action ranking, or publication safety.
It must not be presented as an end-to-end automation success rate.

## Checks

```bash
automation/venv/bin/python -m unittest discover -s scripts/ocr_eval -p 'test_*.py'
git diff --check
```

The initial report must distinguish measured results from incomplete coverage:
current UI versions, light theme, other languages, old/conflicting timestamps,
complete action decisions, and platform packaging need dedicated validation.
