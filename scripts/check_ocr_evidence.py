#!/usr/bin/env python3
"""Replay saved first-post screenshots without starting browsers or publishing.

Use the automation virtualenv and set AUTOMATION_OCR_MODEL_DIR when the source
checkout does not contain EasyOCR model files. Tesseract configuration is shared
with normal automation. JSON records are printed to stdout; evidence is read-only.
"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automation'))

import cv2
from engine.vision import VisionEngine
from modules.reel_top_post import first_header_region, retry_unreadable_header


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--profile', action='append', required=True)
    args = parser.parse_args()
    vision = VisionEngine(SimpleNamespace(profile_id='ocr_evidence_replay'))
    for profile in args.profile:
        if Path(profile).name != profile or profile in {'.', '..'}:
            parser.error('profile must be a directory name')
        evidence = args.runtime_root / 'profiles' / profile / 'automation_evidence'
        images = sorted(evidence.glob('*/*latest*check*.png'))
        if not images:
            print(json.dumps(dict(profile=profile, reason='no_saved_profile_checks')))
        for path in images:
            screen = cv2.imread(str(path))
            if screen is None:
                print(json.dumps(dict(image=str(path), reason='image_unreadable')))
                continue
            height, width = screen.shape[:2]
            items = vision.read_text(screen, region=(int(width*.28), int(height*.16),
                                                     int(width*.67), int(height*.78)), min_confidence=.55)
            region, _ = first_header_region(items, width, height)
            if region is None:
                result = dict(accepted=False, reason='first_post_not_visible')
                reads = []
            else:
                header = vision.read_text(screen, region=region, min_confidence=.55)
                reads, result = retry_unreadable_header(vision, screen, region, header)
            print(json.dumps(dict(profile=profile, image=str(path), result=result,
                                  secondary_used=any(i.get('engine') == 'tesseract' for i in reads))), flush=True)


if __name__ == '__main__':
    main()
