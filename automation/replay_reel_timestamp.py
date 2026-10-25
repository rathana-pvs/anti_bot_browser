"""Read saved screenshots through the real Reel verifier, without browser actions.

Use the installed runtime's Python/models and this checkout's PYTHONPATH to
exercise a source change with the production Paddle v5 -> Tesseract path.
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2

from engine.vision import VisionEngine
from tasks.facebook_reel import FacebookReelTask


class ReplayVision(VisionEngine):
    def __init__(self):
        super().__init__(SimpleNamespace(profile_id='offline_timestamp_replay'))
        self.ocr_engine = 'paddleocr-v5'
        self.reads = []
        self.fallback_calls = 0

    def read_text(self, screen, **kwargs):
        records = super().read_text(screen, **kwargs)
        if self.ocr_device == 'unavailable':
            raise RuntimeError('PaddleOCR v5 is unavailable; this is not a valid replay')
        required = os.environ.get('OCR_REPLAY_REQUIRED_DEVICE')
        if required and self.ocr_device != required:
            raise RuntimeError(f'Replay requires {required}, but OCR used {self.ocr_device}')
        self.reads.append(dict(region=kwargs.get('region'), records=records))
        return records

    def read_text_with_fallback(self, screen, **kwargs):
        self.fallback_calls += 1
        return super().read_text_with_fallback(screen, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('screenshots', nargs='+', type=Path)
    parser.add_argument('--expect-age', action='append', type=int,
                        help='Expected seconds, repeated once per screenshot')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.expect_age is not None and len(args.expect_age) != len(args.screenshots):
        parser.error('Supply one --expect-age for each screenshot')
    vision = ReplayVision()
    results = []
    for index, path in enumerate(args.screenshots):
        screen = cv2.imread(str(path))
        if screen is None:
            parser.error(f'Cannot read screenshot: {path}')
        vision.reads = []
        vision.fallback_calls = 0
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = vision
        task.log = lambda level, message: print(f'{level}: {message}', flush=True)
        accepted = task._latest_reel_is_visible(screen)
        check = getattr(task, 'latest_reel_timestamp_check', {})
        expected = None if args.expect_age is None else args.expect_age[index]
        passed = expected is None or (accepted and check.get('age_seconds') == expected)
        result = dict(screenshot=str(path.resolve()), engine='paddleocr-v5',
                      device=vision.ocr_device, fallback_engine='tesseract',
                      fallback_calls=vision.fallback_calls, accepted=accepted,
                      timestamp_check=check, expected_age_seconds=expected,
                      passed=passed, primary_reads=vision.reads)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != 'primary_reads'}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
