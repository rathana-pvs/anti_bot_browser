"""Recheck reported positive mismatches against HEAD with the same OCR engine.

This isolates parser/locator changes from older evidence and OCR differences.
No working files or modules are replaced, and no browser actions are available.
"""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import types

import cv2

from replay_reel_timestamp import ReplayVision
from replay_recognition_failures import SavedFrame
from tasks import facebook_post, facebook_reel


def source(path):
    return subprocess.run(['git', 'show', f'HEAD:{path}'], check=True,
                          capture_output=True, text=True).stdout


def old_method(path, name, namespace):
    tree = ast.parse(source(path))
    node = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stamps = types.ModuleType('_baseline_timestamp')
    sys.modules[stamps.__name__] = stamps
    exec(source('automation/modules/post_timestamp.py'), stamps.__dict__)
    header = types.ModuleType('_baseline_header')
    sys.modules[header.__name__] = header
    exec(source('automation/modules/reel_top_post.py').replace(
        'from .post_timestamp import', 'from _baseline_timestamp import'), header.__dict__)
    reel_globals = dict(vars(facebook_reel), first_header_region=header.first_header_region,
                        retry_unreadable_header=header.retry_unreadable_header)
    old_reel = old_method('automation/tasks/facebook_reel.py', '_latest_reel_is_visible', reel_globals)
    old_photo = old_method('automation/tasks/facebook_post.py', '_stable_caption_target', dict(vars(facebook_post)))
    frames = [json.loads(line) for line in args.corpus.read_text().splitlines()]
    vision = ReplayVision()
    comparisons = []
    for frame in frames:
        mismatches = [c for c in frame['checks'] if c['positive_regression']]
        if not mismatches:
            continue
        screen = cv2.imread(frame['screenshot'])
        for check in mismatches:
            name = check['detector']
            task = facebook_post.FacebookPostTask.__new__(facebook_post.FacebookPostTask)
            task.vision, task.client = vision, SavedFrame(screen)
            task.log = lambda *a, **k: None
            task.record_locator_telemetry = lambda *a, **k: None
            comparison = dict(screenshot=frame['screenshot'], detector=name, current=check)
            if name in {'reel_timestamp', 'image_timestamp_only'}:
                task = facebook_reel.FacebookReelTask.__new__(facebook_reel.FacebookReelTask)
                task.vision, task.log = vision, lambda *a, **k: None
                comparison.update(baseline_accepted=old_reel(task, screen),
                                  baseline_result=getattr(task, 'latest_reel_timestamp_check', {}))
            elif name == 'photo_caption_locator':
                comparison['baseline_accepted'] = old_photo(task) is not None
            elif name == 'photo_media_locator':
                comparison['baseline_accepted'] = task._stable_profile_media_target() is not None
                comparison['note'] = 'This method was not changed'
            else:
                comparison['note'] = 'Requires manual/recorded-input comparison; no inferred baseline pass'
            comparisons.append(comparison)
            print(json.dumps(comparison), flush=True)
            args.output.write_text(json.dumps(comparisons, indent=2)+'\n')
    if not comparisons:
        args.output.write_text('[]\n')


if __name__ == '__main__':
    main()
