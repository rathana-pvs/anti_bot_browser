"""Offline production-detector replay for recognition failures in a queue snapshot.

Only screenshot reads are available to tasks; no Docker, input, or navigation
methods are provided. Input-entry/clipboard history cannot be reconstructed.
"""
import argparse
import json
import traceback
from pathlib import Path

import cv2

from replay_reel_timestamp import ReplayVision
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask
from modules.publication_result_verifier import ImagePublicationResultVerifier

ERRORS = {'reel_caption_not_entered', 'reel_publish_not_found_after_verification',
          'image_publication_unconfirmed', 'caption_input_not_found', 'photo_button_not_found'}


class SavedFrame:
    def __init__(self, screen):
        self.screen = screen

    def screenshot(self):
        return self.screen.copy()


def replay(vision, execution, post, root):
    directory = Path(execution['evidence_dir'])
    error = execution['error']
    patterns = {
        'reel_publish_not_found_after_verification': '*latest_reel_check_*.png',
        'image_publication_unconfirmed': '*latest_image_check_*.png',
        'reel_caption_not_entered': '*reel_caption_verification.png',
        'caption_input_not_found': '*caption_input_not_found.png',
        'photo_button_not_found': '*photo_button_not_found.png',
    }
    images = sorted(directory.glob(patterns[error]))
    if not images and error == 'reel_caption_not_entered':
        images = sorted(directory.glob('*reel_caption_not_entered.png'))
    result = dict(execution_id=execution['execution_id'], profile_id=execution['profile_id'],
                  original_error=error, evidence_dir=str(directory), frames=[], diagnostics=[])
    for f in sorted(directory.glob('*.json')):
        if any(s in f.name for s in ['caption', 'description']):
            result['diagnostics'].append(dict(file=f.name, data=json.loads(f.read_text())))
    result['input_logs'] = [line for line in execution.get('logs', [])
                            if any(s in line.casefold() for s in ['caption', 'clipboard', 'focus', 'paste'])]
    for image in images:
        vision.reads = []
        vision.fallback_calls = 0
        screen = cv2.imread(str(image))
        if screen is None:
            raise ValueError(f'Cannot read {image}')
        cls = FacebookReelTask if error.startswith('reel_') else FacebookPostTask
        task = cls.__new__(cls)
        task.vision = vision
        task.client = SavedFrame(screen)
        task.caption = execution.get('spun_caption') or post.get('base_caption') or ''
        task.log = lambda *args, **kwargs: None
        task.record_locator_telemetry = lambda *args, **kwargs: None
        task.capture_evidence = lambda *args, **kwargs: None
        frame = dict(screenshot=str(image))
        if error == 'reel_publish_not_found_after_verification':
            frame['accepted'] = task._latest_reel_is_visible(screen)
            frame['check'] = getattr(task, 'latest_reel_timestamp_check', {})
        elif error == 'image_publication_unconfirmed':
            media = Path(post.get('media_file') or '')
            task.media_path = str(media if media.is_absolute() else root/'profiles/shared_media'/media)
            verifier = ImagePublicationResultVerifier(task)
            frame['accepted'] = verifier._post_visible(screen)
            frame['check'] = verifier.last_check
        elif error == 'reel_caption_not_entered':
            for meta in result['diagnostics']:
                if 'description_ready' in meta['file'] and meta['data'].get('target'):
                    task._reel_description_target = tuple(meta['data']['target'])
            frame['description_anchor'] = getattr(task, '_reel_description_target', None)
            if not frame['description_anchor']:
                raise ValueError('Missing original description anchor; cannot reproduce crop')
            score = task._reel_caption_match_confidence(screen)
            words = {w for w in task._normalized_ocr_text(task.caption).split() if len(w) >= 3}
            threshold = 1.0 if len(words) <= 2 else .6
            frame.update(confidence=score, threshold=threshold, accepted=score >= threshold,
                         limitation='Visual caption check only; input/clipboard state not replayed')
        else:
            target = (task._stable_caption_target() if error == 'caption_input_not_found'
                      else task._stable_profile_media_target())
            frame.update(target=target, accepted=target is not None,
                         limitation='Stability checked on repeated saved frame, not live frames')
        frame.update(primary_reads=vision.reads, fallback_calls=vision.fallback_calls,
                     device=vision.ocr_device)
        result['frames'].append(frame)
    result['replay_recognized_any_frame'] = any(f['accepted'] for f in result['frames'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    queue = json.loads((args.runtime/'data/posting_queue.json').read_text())
    vision = ReplayVision()
    results = []
    for batch in queue.get('daily_batches', []):
        for post in batch.get('posts', []):
            for execution in post.get('executions', []):
                if execution.get('error') not in ERRORS:
                    continue
                try:
                    result = replay(vision, execution, post, args.runtime)
                except Exception:
                    result = dict(execution_id=execution['execution_id'], original_error=execution['error'],
                                  replay_error=traceback.format_exc())
                results.append(result)
                print(json.dumps({k: v for k, v in result.items()
                                  if k in {'execution_id', 'original_error', 'replay_recognized_any_frame', 'replay_error'}}), flush=True)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(dict(engine='paddleocr-v5', fallback='tesseract',
                    results=results), indent=2) + '\n')
    return 1 if any('replay_error' in r or not r.get('frames') for r in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
