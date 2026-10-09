"""CLI adapter for the template-driven Facebook Live task."""
import argparse
import json
import sys

from engine.live_brain import pinned_live_brain
from tasks.facebook_live import FacebookLiveTask


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--action', choices=['prepare', 'start', 'end'], required=True)
    parser.add_argument('--producer-url', required=True)
    parser.add_argument('--brain-package')
    parser.add_argument('--brain-digest')
    args = parser.parse_args()
    output = sys.stdout
    sys.stdout = sys.stderr
    try:
        if bool(args.brain_package) != bool(args.brain_digest):
            raise RuntimeError('A Live workflow pin requires both package and digest')
        package = pinned_live_brain(args.brain_package, args.brain_digest) if args.brain_package else None
        task = FacebookLiveTask(args.profile, package)
        details = json.loads(sys.stdin.read(65536) or '{}')
        result = task.run(args.action, args.producer_url, title=details.get('title', ''), caption=details.get('caption', ''), pinned_comment=details.get('pinned_comment'))
    except Exception as error:
        result = {'success': False, 'error': str(error) if isinstance(error, RuntimeError) else 'Facebook Live visual check failed'}
    print(json.dumps(result), file=output, flush=True)


if __name__ == '__main__':
    main()
