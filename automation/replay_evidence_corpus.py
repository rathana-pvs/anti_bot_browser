"""Audit every saved frame and replay changed detectors at applicable stages.

Run with the installed Python/models and source PYTHONPATH. Workers never have
browser/input methods: SavedFrame supplies only screenshot copies. Successful
historical assertions are checked; old failures are reported separately, not
treated as ground truth. Missing input context is explicitly marked.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
from pathlib import Path
import time
import traceback

import cv2

from replay_reel_timestamp import ReplayVision
from replay_recognition_failures import SavedFrame
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask
from modules.publication_result_verifier import ImagePublicationResultVerifier
from engine.screen_state import FacebookStateRecognizer

VISION = None


def entry_plan_field(stem, meta, metadata):
    """The shared caption_entry_plan label can also describe a first comment."""
    if meta.get('field') in {'caption', 'comment'}:
        return meta['field']
    number = stem.partition('_')[0]
    if not number.isdigit():
        return 'unknown'
    earlier = [(int(key.partition('_')[0]), value) for key, value in metadata.items()
               if key.partition('_')[0].isdigit() and int(key.partition('_')[0]) < int(number)]
    if earlier and max(earlier, key=lambda item: item[0])[1].get('label') == 'before_first_comment':
        return 'comment'
    return 'caption'


def check(name, accepted, expected=None, **details):
    return dict(detector=name, accepted=bool(accepted), historical_positive=expected is True,
                positive_regression=expected is True and not accepted, **details)


def run_frames(job):
    global VISION
    if VISION is None:
        VISION = ReplayVision()
    vision = VISION
    run, context = Path(job['run']), job['context']
    result_file = run/'result.json'
    result = json.loads(result_file.read_text()) if result_file.exists() else {}
    telemetry_file = run/'telemetry.json'
    env = json.loads(telemetry_file.read_text()).get('environment', {}) if telemetry_file.exists() else {}
    vision.theme = env.get('theme') or 'unknown'
    metadata = {f.stem: json.loads(f.read_text()) for f in run.glob('*.json')
                if f.name not in {'result.json', 'telemetry.json', 'stage.json'}}
    description = next((m.get('target') for m in metadata.values()
                        if m.get('label') == 'reel_description_ready'), None)
    is_reel = run.name.endswith('_FacebookReelTask')
    frames = []
    for path in sorted(run.glob('*.png')):
        if job.get('screenshots') is not None and str(path) not in job['screenshots']:
            continue
        started = time.monotonic()
        entry = dict(screenshot=str(path), outcome=result.get('status', 'missing_result'),
                     task=run.name.split('Z_')[-1], checks=[], limitations=[])
        try:
            screen = cv2.imread(str(path))
            if screen is None:
                raise ValueError('Screenshot decoding failed')
            entry['shape'] = list(screen.shape)
            meta = metadata.get(path.stem, {})
            label = meta.get('label', path.stem.partition('_')[2])
            entry['label'] = label
            vision.reads = []
            vision.fallback_calls = 0
            # Changed-stage detectors supply their own production OCR crops.
            # Other stages get a bounded OCR smoke read (target/action region
            # when recorded), not a redundant full-page read or action replay.
            task = (FacebookReelTask if is_reel else FacebookPostTask).__new__(
                FacebookReelTask if is_reel else FacebookPostTask)
            task.vision = vision
            task.client = SavedFrame(screen)
            task.log = lambda *a, **k: None
            task.capture_evidence = lambda *a, **k: None
            task.record_locator_telemetry = lambda *a, **k: None
            if context:
                task.caption = context['caption']
                task.media_path = context['media_path']
            if description:
                task._reel_description_target = tuple(description)
            if label.startswith('latest_reel_check_') or label == 'reel_publish_not_found_after_verification':
                accepted = task._latest_reel_is_visible(screen)
                entry['checks'].append(check('reel_timestamp', accepted,
                    meta.get('latest_reel_visible'), result=getattr(task, 'latest_reel_timestamp_check', {})))
            elif label.startswith('latest_image_check_') or label == 'image_post_check':
                if context:
                    verifier = ImagePublicationResultVerifier(task)
                    accepted = verifier._post_visible(screen)
                    expected = meta.get('latest_image_visible')
                    if label == 'image_post_check':
                        expected = meta.get('reason') == 'recent_matching_first_post'
                    entry['checks'].append(check('image_publication', accepted, expected,
                                                  result=verifier.last_check))
                else:
                    entry['limitations'].append('No exact historical caption/media mapping; identity check not replayable')
                    # Timestamp geometry can still be replayed without making
                    # an identity claim for this historical photo.
                    reel = FacebookReelTask.__new__(FacebookReelTask)
                    reel.vision, reel.log = vision, task.log
                    accepted = reel._latest_reel_is_visible(screen)
                    entry['checks'].append(check('image_timestamp_only', accepted,
                        meta.get('latest_image_visible'), result=getattr(reel, 'latest_reel_timestamp_check', {})))
            elif label in {'reel_description_ready', 'reel_caption_verification', 'reel_caption_not_entered'}:
                placeholder = task._find_reel_description_target(require_placeholder=True)
                # The field must remain provably empty before initial entry.
                # A cached description point is deliberately set above.
                if label == 'reel_description_ready':
                    entry['checks'].append(check('fresh_empty_placeholder', placeholder is not None,
                                                  True, target=placeholder))
                else:
                    entry['checks'].append(check('populated_field_not_treated_as_empty', placeholder is None,
                        meta.get('caption_visible'), placeholder=placeholder))
                    if context and description:
                        score = task._reel_caption_match_confidence(screen)
                        words = {w for w in task._normalized_ocr_text(task.caption).split() if len(w) >= 3}
                        threshold = 1.0 if len(words) <= 2 else .6
                        entry['checks'].append(check('visual_caption_match', score >= threshold,
                            meta.get('caption_visible'), score=score, threshold=threshold))
                    else:
                        entry['limitations'].append('Exact historical caption unavailable; clipboard/entry not replayed')
            elif label == 'caption_entry_plan' and entry_plan_field(path.stem, meta, metadata) == 'comment':
                target = task._find_first_comment_input(screen=screen)
                entry['checks'].append(check('comment_input_locator', target is not None, True, target=target))
            elif not is_reel and label in {'caption_entry_plan', 'caption_input_not_found'}:
                target = task._stable_caption_target()
                entry['checks'].append(check('photo_caption_locator', target is not None,
                                              label == 'caption_entry_plan', target=target))
                # Confirm that a located field also passes the insertion guard.
                if target is not None:
                    state = FacebookStateRecognizer(vision).observe(screen).state.value
                    entry['checks'].append(check('photo_composer_guard',
                        state in {'composer_open', 'media_ready', 'post_enabled'},
                        label == 'caption_entry_plan', state=state))
            elif label in {'before_click_photo_video', 'photo_button_not_found'}:
                target = task._stable_profile_media_target()
                entry['checks'].append(check('photo_media_locator', target is not None,
                                              label == 'before_click_photo_video', target=target))
            if not vision.reads:
                reference = job.get('smoke_regions', {})
                region = meta.get('action_region')
                target = meta.get('target') or meta.get('open_target')
                if region is None and target and len(target) == 2:
                    tx, ty = target
                    region = (max(0, int(tx)-120), max(0, int(ty)-35), 240, 70)
                if region is None:
                    region = 'session_gate' if label in {'login_check', 'preparation_ready'} else 'browser_dialog'
                if str(path) in reference:
                    region = reference[str(path)]
                vision.read_text(screen, region=region, min_confidence=.18)
                entry['coverage'] = 'ocr_smoke_only'
            else:
                entry['coverage'] = 'detector_replay'
            entry['ocr_items'] = sum(len(read['records']) for read in vision.reads)
            entry.update(device=vision.ocr_device, fallback_calls=vision.fallback_calls,
                         reads=vision.reads)
        except Exception:
            entry['error'] = traceback.format_exc()
        entry['duration_seconds'] = round(time.monotonic() - started, 3)
        frames.append(entry)
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--manifest', type=Path, help='Replay only screenshots from a prior frames.jsonl checkpoint')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    journal = args.output/'frames.jsonl'
    existing = [json.loads(line) for line in journal.read_text().splitlines()] if args.resume and journal.exists() else []
    done_paths = {f['screenshot'] for f in existing}
    queue = json.loads((args.runtime/'data/posting_queue.json').read_text())
    contexts = {}
    reference = [json.loads(line) for line in args.manifest.read_text().splitlines()] if args.manifest else None
    selected = {f['screenshot'] for f in reference} if reference is not None else None
    smoke_regions = {f['screenshot']: f['reads'][0]['region'] for f in reference
                     if f.get('reads') and not f['checks']} if reference is not None else {}
    for batch in queue.get('daily_batches', []):
        for post in batch.get('posts', []):
            for ex in post.get('executions', []):
                if ex.get('evidence_dir'):
                    media = Path(post.get('media_file') or '')
                    if not media.is_absolute():
                        media = args.runtime/'profiles/shared_media'/media
                    contexts[ex['evidence_dir']] = dict(caption=ex.get('spun_caption') or post.get('base_caption') or '',
                                                        media_path=str(media))
    jobs = [dict(run=str(run), context=contexts.get(str(run)), screenshots=selected, smoke_regions=smoke_regions)
            for run in sorted((args.runtime/'profiles').glob('*/automation_evidence/*'))
            if list(run.glob('*.png')) and not all(str(p) in done_paths for p in run.glob('*.png'))
            and (selected is None or any(str(p) in selected for p in run.glob('*.png')))]
    results = existing
    with journal.open('a' if args.resume else 'w') as stream:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {pool.submit(run_frames, job): job for job in jobs}
            for future in as_completed(futures):
                frames = future.result()
                for frame in frames:
                    if frame['screenshot'] in done_paths:
                        continue
                    results.append(frame)
                    stream.write(json.dumps(frame) + '\n')
                stream.flush()
                checks = [c for f in frames for c in f['checks']]
                print(json.dumps(dict(completed_frames=len(results), total_checks=sum(len(f['checks']) for f in results),
                                      regressions=sum(c['positive_regression'] for f in results for c in f['checks']),
                                      run=futures[future]['run'], errors=sum('error' in f for f in results))), flush=True)
    checks = [c for frame in results for c in frame['checks']]
    summary = dict(engine='paddleocr-v5', fallback='tesseract', frames=len(results),
                   runs=len({str(Path(f['screenshot']).parent) for f in results}),
                   checks=len(checks), historical_positive_checks=sum(c['historical_positive'] for c in checks),
                   positive_regressions=[dict(screenshot=f['screenshot'], check=c) for f in results
                                         for c in f['checks'] if c['positive_regression']],
                   frame_errors=[dict(screenshot=f['screenshot'], error=f['error']) for f in results if 'error' in f],
                   fallback_calls=sum(f.get('fallback_calls', 0) for f in results),
                   frames_with_limitations=sum(bool(f['limitations']) for f in results))
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in {'positive_regressions', 'frame_errors'}}), flush=True)
    return 1 if summary['positive_regressions'] or summary['frame_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
