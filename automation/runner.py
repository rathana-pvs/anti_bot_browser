"""Automation Task Runner CLI.

Invoked by the backend server or command line to execute browser tasks
against specific profile containers. Outputs structured JSON log lines to stdout.
"""

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", message=".*pin_memory.*")
warnings.filterwarnings("ignore", message=".*torch.quantize_per_tensor.*")

# Ensure automation root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from tasks.facebook_warming import FacebookWarmingTask
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask
from tasks.facebook_comment import FacebookCommentTask
from tasks.facebook_preparation import FacebookPreparationTask


def main():
    parser = argparse.ArgumentParser(description="Isolated Browser Automation Runner")
    parser.add_argument("--profile", required=True, help="Profile ID (e.g. profile_001)")
    parser.add_argument("--task", required=True, choices=["warming", "preparation", "post", "reel", "comment"], help="Task to execute")
    parser.add_argument("--caption", default="", help="Post caption (required for post/reel task)")
    parser.add_argument("--comment-link", default=None, help="Destination URL for first comment")
    parser.add_argument("--post-url", default=None, help="Target post URL for standalone comment task")
    parser.add_argument("--media", default=None, help="Path to media file")
    parser.add_argument("--scrolls", type=int, default=4, help="Scroll count for warming task")
    parser.add_argument("--preparation-mode", choices=["brief", "extended"], default="brief")

    args = parser.parse_args()

    result = {
        "success": False,
        "status": "failed",
        "profile_id": args.profile,
        "task": args.task,
        "logs": [],
    }

    try:
        if args.task == "preparation":
            task = FacebookPreparationTask(profile_id=args.profile, mode=args.preparation_mode)
            success = task.run()
            result["success"] = success
            result["logs"] = task.logs
        elif args.task == "warming":
            task = FacebookWarmingTask(profile_id=args.profile, scroll_count=args.scrolls)
            success = task.run()
            result["success"] = success
            result["logs"] = task.logs
        elif args.task == "post":
            if not args.caption and not args.media:
                print(json.dumps({"error": "Missing --caption or --media for post task"}))
                sys.exit(1)
            task = FacebookPostTask(
                profile_id=args.profile,
                caption=args.caption,
                comment_link=args.comment_link,
                media_path=args.media,
            )
            success = task.run()
            result["success"] = success
            result["logs"] = task.logs
        elif args.task == "reel":
            if not args.media:
                print(json.dumps({"error": "Missing --media for reel task"}))
                sys.exit(1)
            task = FacebookReelTask(
                profile_id=args.profile,
                video_path=args.media,
                caption=args.caption,
                comment_link=args.comment_link,
            )
            success = task.run()
            result["success"] = success
            result["logs"] = task.logs
        elif args.task == "comment":
            if not args.comment_link:
                print(json.dumps({"error": "Missing --comment-link for comment task"}))
                sys.exit(1)
            task = FacebookCommentTask(
                profile_id=args.profile,
                comment_text=args.comment_link,
                post_url=args.post_url,
            )
            success = task.run()
            result["success"] = success
            result["logs"] = task.logs

        task_status = getattr(task, "result_status", "running")
        current_stage = getattr(task, "current_stage", "unknown")
        stage_history = getattr(task, "stage_history", [])

        if task_status == "running":
            task_status = "completed" if success else ("uncertain" if current_stage in ("publish_clicked", "verifying") else "failed_before_publish")

        result["status"] = task_status
        result["current_stage"] = current_stage
        result["stage_history"] = stage_history
        result["error"] = getattr(task, "result_error", None)
        extra = getattr(task, "result_extra", {})
        if isinstance(extra, dict):
            result.update(extra)
        if "telemetry" not in result:
            telemetry = getattr(task, "telemetry", None)
            if telemetry is not None:
                try:
                    result["telemetry"] = telemetry.finalize(task_status)
                except Exception as exc:
                    task.log("WARN", f"Could not finalize runner telemetry: {exc}")
        result["post_url"] = result.get("post_url", None)
        result["post_url_verified_at"] = result.get("post_url_verified_at", None)
        result["post_match_confidence"] = result.get("post_match_confidence", None)
        evidence = getattr(task, "evidence", None)
        result["evidence_dir"] = getattr(evidence, "directory", None)

        print(json.dumps(result), flush=True)
        if result["status"] in ("uncertain", "needs_review"):
            sys.exit(2)
        sys.exit(0 if result["success"] else 1)

    except Exception as e:
        task_stage = getattr(task, "current_stage", "unknown") if "task" in locals() else "unknown"
        result["current_stage"] = task_stage
        result["stage_history"] = getattr(task, "stage_history", []) if "task" in locals() else []
        telemetry = getattr(task, "telemetry", None) if "task" in locals() else None
        # Invariant: If process failed after publish_clicked, status MUST be uncertain, never failed!
        if task_stage in ("publish_clicked", "verifying"):
            result["status"] = "uncertain"
            result["error"] = f"Fatal error after publish click: {e}"
            if telemetry is not None:
                try:
                    result["telemetry"] = telemetry.finalize(result["status"])
                except Exception:
                    pass
            print(json.dumps(result), flush=True)
            sys.exit(2)
        else:
            result["status"] = "failed_before_publish"
            result["error"] = str(e)
            if telemetry is not None:
                try:
                    result["telemetry"] = telemetry.finalize(result["status"])
                except Exception:
                    pass
            print(json.dumps(result), flush=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
