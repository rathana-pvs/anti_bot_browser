"""CLI adapter for the template-driven Facebook Live task."""
import argparse
import json
import os
import sys

from engine.live_brain import pinned_live_brain
from tasks.facebook_live import FacebookLiveTask
from live_comment_status import COMMENT_CODES
from live_account_status import ACCOUNT_BLOCK_ERRORS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--action', choices=['prepare', 'start', 'end', 'inspect', 'comment', 'finish'], required=True)
    parser.add_argument('--producer-url', required=True)
    parser.add_argument('--brain-package')
    parser.add_argument('--brain-digest')
    args = parser.parse_args()
    output = sys.stdout
    sys.stdout = sys.stderr
    task = None
    job_folder = os.environ.get('LIVE_EVIDENCE_DIR') or None  # the Live job's own screenshot folder
    try:
        if bool(args.brain_package) != bool(args.brain_digest):
            raise RuntimeError('A Live workflow pin requires both package and digest')
        package = pinned_live_brain(args.brain_package, args.brain_digest) if args.brain_package else None
        task = FacebookLiveTask(args.profile, package)
        task.on_event = lambda event: print(json.dumps({'event': event}), file=output, flush=True)
        details = json.loads(sys.stdin.read(65536) or '{}')
        task.comment_after_end = details.get('comment_after_end') is True
        result = task.run(args.action, args.producer_url, title=details.get('title', ''), caption=details.get('caption', ''), pinned_comment=details.get('pinned_comment'))
        # Each step that worked keeps the frame it confirmed: ready, live, ended, the comment, and
        # the read-only inspection (kept even when it saw neither live nor ended).
        # The comment's last observation is the frame where the posted comment matched.
        saved = {'prepare': 'prepared', 'start': 'live', 'end': 'ended', 'comment': 'commented'}
        worked = result.get('success') is True and result.get('state') == saved.get(args.action)
        if args.action == 'inspect' or worked:
            try:
                directory = task.failure_evidence.save(
                    args.action, status='observed' if args.action == 'inspect' else 'confirmed',
                    state=result.get('state'), directory=job_folder)
                if directory:
                    result['evidence_dir'] = directory
                elif args.action == 'comment':
                    result['evidence_capture_failed'] = True
            except Exception:
                result['evidence_capture_failed'] = True
    except Exception as error:
        result = {'success': False, 'error': str(error) if isinstance(error, RuntimeError) else 'Facebook Live visual check failed'}
        if getattr(error, 'account_block', None) in ACCOUNT_BLOCK_ERRORS:
            result['account_block'] = error.account_block
        code = getattr(error, 'failure_code', None)
        if code in COMMENT_CODES:
            result['failure_code'] = code
        if task is not None and args.action != 'finish':
            try:
                directory = task.failure_evidence.save(args.action, directory=job_folder)
                if directory:
                    result['evidence_dir'] = directory
            except Exception:
                result['evidence_capture_failed'] = True
    print(json.dumps(result), file=output, flush=True)


if __name__ == '__main__':
    main()
