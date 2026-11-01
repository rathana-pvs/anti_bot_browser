"""Phone job runner: one post, reel or first comment on one phone account (an Android user).

Prints one JSON line per event, then the result as the last line, like ``runner.py``. The caller holds
the account and phone locks and has checked the account's network before starting it.

  phone_runner.py --serial S --user 11 --task photo|reel --media FILE --caption TEXT [--comment TEXT]
  phone_runner.py --serial S --user 11 --task comment --kind photo|reel --caption TEXT --comment TEXT
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parent
sys.path.insert(0, str(AUTOMATION))
sys.path.insert(1, str(AUTOMATION.parent))  # backend.services: adb, scrcpy and the companion app

from engine.runtime_paths import runtime_root  # noqa: E402
from phone.device import PhoneDevice  # noqa: E402
from phone.flows import PostItem  # noqa: E402
from phone.job import run_comment, run_post  # noqa: E402


def emit(**event) -> None:
    event["t"] = time.strftime("%H:%M:%S")
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def main(args) -> dict:
    evidence = Path(args.evidence_dir or runtime_root() / "data" / "phone_evidence" / time.strftime("%Y%m%d_%H%M%S"))
    device = PhoneDevice(args.serial, args.user, evidence, emit)
    await device.open()
    try:
        if args.task == "comment":
            item = PostItem(args.kind, args.caption, args.comment)
            return await run_comment(device, item, emit, human_only=args.human_only)
        item = PostItem(args.task, args.caption, args.comment or "")
        return await run_post(device, item, args.media, emit, human_only=args.human_only,
                              comment_now=not args.comment_later)
    finally:
        await device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phone job runner")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--user", type=int, required=True, help="Android user of the account")
    parser.add_argument("--task", required=True, choices=["photo", "reel", "comment"])
    parser.add_argument("--kind", choices=["photo", "reel"], default="photo", help="comment task: the post's kind")
    parser.add_argument("--media")
    parser.add_argument("--caption", required=True)
    parser.add_argument("--comment", default="")
    parser.add_argument("--human-only", action="store_true", help="never use shortcut intents")
    parser.add_argument("--comment-later", action="store_true", help="leave the first comment pending")
    parser.add_argument("--evidence-dir")
    options = parser.parse_args()
    if options.task != "comment" and not (options.media and os.path.isfile(options.media)):
        parser.error("--media must be an existing file for photo and reel tasks")
    if options.task == "comment" and not options.comment:
        parser.error("--comment is required for the comment task")
    try:
        outcome = asyncio.run(main(options))
    except Exception as error:  # run_post reports its own errors, so this is the connection itself
        outcome = {"status": "failed", "stage": "connect", "reason": f"{type(error).__name__}: {error}"[:400]}
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    sys.exit(0 if outcome.get("status") == "published" else 1)
