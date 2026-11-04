"""CLI adapter for the template-driven Facebook Live task."""
import argparse
import json
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
    try:
        if bool(args.brain_package) != bool(args.brain_digest):
            raise RuntimeError('A Live workflow pin requires both package and digest')
        package = pinned_live_brain(args.brain_package, args.brain_digest) if args.brain_package else None
        task = FacebookLiveTask(args.profile, package)
        task.on_event = lambda event: print(json.dumps({'event': event}), file=output, flush=True)
        details = json.loads(sys.stdin.read(65536) or '{}')
        task.comment_after_end = details.get('comment_after_end') is True
        result = task.run(args.action, args.producer_url, title=details.get('title', ''), caption=details.get('caption', ''), pinned_comment=details.get('pinned_comment'))
        # Inspection is read-only and is requested after the sender stops or
        # another browser helper is cancelled. Retain even an unconfirmed frame.
        comment_confirmed = (args.action == 'comment' and
                             result.get('success') is True and result.get('state') == 'commented')
        if args.action == 'inspect' or comment_confirmed:
            try:
                # The comment helper's last observation is the frame where the
                # posted comment matched. Save it before container cleanup.
                directory = task.failure_evidence.save(
                    args.action, status='confirmed' if comment_confirmed else 'observed')
                if directory:
                    result['evidence_dir'] = directory
                elif comment_confirmed:
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
                directory = task.failure_evidence.save(args.action)
                if directory:
                    result['evidence_dir'] = directory
            except Exception:
                result['evidence_capture_failed'] = True
    print(json.dumps(result), file=output, flush=True)


if __name__ == '__main__':
    main()
