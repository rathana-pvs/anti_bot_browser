"""First comment on a just-published post: find the input on the profile
feed or a verified permalink, enter the comment, and confirm it rendered."""

import random
import re
import time

from engine.vision import VisionEngine
from skills.comment_delivery import CHECK_SECONDS, CommentDelivery


def first_comment_search_regions(screen):
    """Return the evidence-backed local crop and the existing broad fallback."""
    height, width = screen.shape[:2]
    narrow = (
        int(width * 0.53),
        int(height * 0.62),
        int(width * 0.38),
        int(height * 0.29),
    )
    broad = (
        max(0, int(width * 0.28)),
        0,
        min(width - int(width * 0.28), int(width * 0.67)),
        height,
    )
    return narrow, broad


def submitted_comment_region(screen, comment_box_pos):
    """Crop the input, delivery notices above it, and the input after it moves down."""
    height, width = screen.shape[:2]
    _anchor_x, anchor_y = comment_box_pos
    x1 = int(width * 0.28)
    y1 = max(0, int(anchor_y - height * 0.40))
    x2 = int(width * 0.95)
    y2 = min(height, int(anchor_y + height * 0.25))
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def comment_box_empty(ocr_items: list[dict], comment_box_pos, screen_height: int) -> bool:
    """A placeholder shows only in an empty box, and a new comment never moves it up."""
    floor = comment_box_pos[1] - screen_height * 0.05
    return any(
        VisionEngine.is_comment_input_text(item.get("text", ""))
        and item.get("center", (0, floor - 1))[1] >= floor
        for item in ocr_items
    )


class FirstComment:
    def __init__(self, task):
        self.task = task

    def find_input(self, screen=None, region=None):
        """Locate the 'Comment as ...' or 'Write a comment...' input field."""
        task = self.task
        if hasattr(task.vision, "find_comment_input"):
            regions = [region] if region is not None else None
            res = task.vision.find_comment_input(screen=screen, regions=regions)
            if res is not None and type(res).__name__ not in ("Mock", "MagicMock"):
                return res
        # Fallback / mock support
        candidates = []
        screen = task.client.screenshot() if screen is None and hasattr(task, "client") and task.client else screen
        for item in task.vision.read_text(screen, region=region, min_confidence=0.15):
            if VisionEngine.is_comment_input_text(item.get("text", "")):
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["center"][1])
        return candidates[0]["center"]

    def find_action(self, screen):
        """Return the topmost exact Comment action in the profile post stream."""
        task = self.task
        height, width = screen.shape[:2]
        region = (
            max(0, int(width * 0.28)),
            0,
            min(width - int(width * 0.28), 1050),
            height,
        )
        candidates = []
        for item in task.vision.read_text(screen, region=region, min_confidence=0.35):
            normalized = re.sub(r"[^a-z]+", " ", item.get("text", "").casefold()).strip()
            if normalized == "comment":
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.get("center", (0, height + 1))[1])
        return candidates[0]

    def find_targets(self, screen, *, allow_broad_fallback=True):
        """Search the expected comment crop, then the broad post/feed area."""
        task = self.task
        height = screen.shape[0]
        narrow, broad = task._first_comment_search_regions(screen)
        regions = [("narrow", narrow)]
        if allow_broad_fallback:
            regions.append(("broad", broad))

        for tier, region in regions:
            inputs = []
            actions = []
            for item in task.vision.read_text(screen, region=region, min_confidence=0.15):
                normalized = re.sub(r"[^a-z0-9]+", " ", item.get("text", "").casefold()).strip()
                if VisionEngine.is_comment_input_text(item.get("text", "")):
                    inputs.append(item)
                elif normalized == "comment":
                    actions.append(item)

            inputs.sort(key=lambda item: item.get("center", (0, height + 1))[1])
            actions.sort(key=lambda item: item.get("center", (0, height + 1))[1])
            input_target = inputs[0].get("center") if inputs else None
            action_target = actions[0] if actions else None
            if input_target or action_target:
                task._last_comment_search_tier = tier
                return input_target, action_target

        task._last_comment_search_tier = "not_found"
        return None, None

    def open_profile_input(self, *, navigate: bool = True):
        """
        Primary comment targeting method: navigate to the profile and select the
        topmost comment field/action belonging to the first visible post.

        Profiles managed by this application do not pin posts, so the first post
        is the intended newly published item once the profile feed has refreshed.
        """
        task = self.task
        task.log("STEP", "Primary comment method: opening the profile's first post...")
        if navigate:
            task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
        else:
            task.log("INFO", "Reusing the verified post-publication profile feed for comment targeting.")

        # Move mouse over main post feed column (x ~ 1150, y ~ 500) so mouse wheel / keys scroll feed
        task.human.move_to(1150, 500)

        # Comment success is more important than OCR cost here. Tall Reel cards
        # can require substantially more vertical travel than photo/text posts,
        # so every scan uses all detection regions and the bounded search goes
        # far enough to expose the first card's action row.
        for scan in range(1, 15):
            screen = task.client.screenshot()
            target, action_btn = task._find_first_comment_targets(
                screen,
                allow_broad_fallback=True,
            )
            search_tier = getattr(task, "_last_comment_search_tier", "unknown")
            task.log_decision(
                "Find 'Comment as ...' field",
                "post card comment input ('Comment as ...' or 'Write a comment...')",
                f"scan={scan}, tier={search_tier}, target={target}",
                "click comment field" if target else "scroll and scan again",
                level="INFO",
            )
            if target:
                return target, screen

            # The Comment action/icon is only a landmark. Clicking it can open a
            # post overlay without focusing the editor, after which pasted text
            # is lost. Move the feed just enough to expose the actual comment
            # input below the action row, then require that input on a later
            # scan before any text-entry click occurs.
            if action_btn:
                action_source = action_btn.get("source", "ocr_comment_label")
                task.capture_evidence(
                    "comment_action_observed",
                    screen,
                    target=list(action_btn["center"]),
                    action_source=action_source,
                    scan=scan,
                )
                task.log(
                    "INFO",
                    f"Observed '{action_source}' at {action_btn['center']}; scrolling to the comment input without clicking the action.",
                )
                task.human.scroll("down", notches=1)
                time.sleep(0.8)
                continue

            if scan == 1:
                time.sleep(0.8)
                continue

            # Move just enough to reveal the remainder of the first post. Large
            # page jumps could make a later post become the topmost visible card.
            task.human.scroll("down", notches=2)
            time.sleep(0.8)

        task.capture_evidence(
            "comment_input_not_found",
            task.client.screenshot(),
            scans=14,
            broad_post_area=True,
            full_screen_fallback=False,
        )
        return None, None

    def open_permalink_input(self, post_url: str | None):
        """Fallback comment target used only before any comment submission."""
        task = self.task
        if not post_url:
            return None, None
        is_valid, clean_url = task.validate_facebook_permalink(post_url)
        if not is_valid or not clean_url:
            task.log("WARN", f"Comment permalink fallback rejected an invalid URL: {post_url}")
            return None, None

        task.log("STEP", f"Fallback comment method: opening verified permalink {clean_url}")
        try:
            task.navigate_to(clean_url, wait_seconds=3.0)
            for scan in range(1, 4):
                screen = task.client.screenshot()
                _narrow, broad = task._first_comment_search_regions(screen)
                target = task._find_first_comment_input(screen, region=broad)
                task.log_decision(
                    "Find permalink comment field",
                    "'Comment as ...' field on the verified permalink",
                    f"scan={scan}, target={target}",
                    "use permalink field" if target else "wait and scan again",
                    level="INFO",
                )
                if target:
                    return target, screen
                time.sleep(1.2)
        except Exception as exc:
            task.log("WARN", f"Could not use the permalink comment fallback: {exc}")
        return None, None

    def post(
        self,
        comment_link: str,
        post_url: str | None = None,
        *,
        reuse_profile_page: bool = False,
        prefer_permalink: bool = False,
        warm_down_after_submit: bool = True,
    ) -> str:
        """
        Locate the post's 'Comment as ...' field, click it, paste comment_link, and submit once with Return.
        Returns: 'submitted_unverified' on submission, or 'failed_input_not_found'.
        """
        task = self.task
        task.set_stage("commenting")
        task.log("STEP", "Locating 'Comment as ...' field for first comment...")
        time.sleep(1.5)
        task.last_comment_method = None
        comment_box_pos = None
        comment_screen = None
        if not prefer_permalink:
            if reuse_profile_page:
                comment_box_pos, comment_screen = task._open_profile_first_comment_input(
                    navigate=False
                )
            else:
                comment_box_pos, comment_screen = task._open_profile_first_comment_input()
            if comment_box_pos:
                task.last_comment_method = "profile_first_post"
        if not comment_box_pos and reuse_profile_page and not prefer_permalink:
            task.log(
                "INFO",
                "The reused profile feed did not expose a comment field; refreshing and running one full second pass.",
            )
            comment_box_pos, comment_screen = task._open_profile_first_comment_input(
                navigate=True
            )
            if comment_box_pos:
                task.last_comment_method = "profile_first_post_refresh"
        if not comment_box_pos and post_url:
            task.log("WARN", "Primary first-post comment target was not found; trying the verified permalink before submission.")
            comment_box_pos, comment_screen = task._open_permalink_comment_input(post_url)
            if comment_box_pos:
                task.last_comment_method = "verified_permalink_fallback"
        if not comment_box_pos:
            task.log_decision(
                "Submit first comment",
                "'Comment as ...' field under post",
                "no verified 'Comment as' field found after scanning",
                "keep content published and skip comment",
                level="WARN",
            )
            return "failed_input_not_found"

        if comment_screen is None:
            comment_screen = task.client.screenshot()

        task.capture_evidence(
            "before_first_comment",
            comment_screen,
            target=list(comment_box_pos),
            comment_method=task.last_comment_method,
        )
        task.log_decision(
            "Submit first comment",
            "'Comment as ...' field under post",
            f"method={task.last_comment_method}, target={comment_box_pos}",
            "click 'Comment as ...', paste configured comment, and submit once",
        )
        task.log("STEP", f"Clicking 'Comment as ...' at {comment_box_pos}...")
        entry = task.composer_entry.enter(comment_link, comment_box_pos, field="comment",
                                          empty_confirmed=lambda: task._find_first_comment_input() is not None,
                                          deadline=time.monotonic() + 60)
        if not entry.verified:
            task.log("WARN", f"First comment entry stopped: {entry.status}; no submission sent.")
            return "failed_input_not_found"
        # Submission remains outside content entry and is sent once.
        owner = task.composer_entry.last_owner
        if task.input_cancelled:
            return "failed_input_not_found"
        try:
            task.composer_entry.waiter(owner).check()
        except Exception:
            return "failed_input_not_found"
        task.human.key_press("Return")
        task.log("INFO", "First comment was submitted once; waiting for Facebook's Posting indicator to clear.")

        after_comment = None
        delivery = CommentDelivery()
        for _ in range(30):
            time.sleep(CHECK_SECONDS)
            after_comment = task.client.screenshot()
            comment_items = task.vision.read_text(
                after_comment,
                region=task._submitted_comment_region(after_comment, comment_box_pos),
                min_confidence=0.15,
            )
            # Never resubmit automatically, whatever this check finds.
            box_empty = comment_box_empty(comment_items, comment_box_pos, after_comment.shape[0])
            if delivery.observe(comment_items, box_empty):
                break

        if after_comment is None:
            after_comment = task.client.screenshot()
        comment_status = (
            "submitted_verified" if delivery.finished
            else "submission_pending" if delivery.state == "pending"
            else "submitted_unverified"
        )
        task.capture_evidence(
            "after_first_comment",
            after_comment,
            comment_status=comment_status,
            comment_delivery_state=delivery.state,
            comment_method=task.last_comment_method,
        )

        if comment_status == "submission_pending":
            task.log("WARN", "First comment is still submitting; leaving the post modal open and not retrying.")
            return "submission_pending"

        if comment_status == "submitted_verified":
            task.log("SUCCESS", "First comment finished: the box is empty and no delivery notice remains.")
        else:
            task.log(
                "WARN",
                f"First comment did not finish cleanly ({delivery.state}); no automatic retry will be attempted.",
            )

        if not warm_down_after_submit:
            task.log("INFO", "Comment verification complete; returning immediately for container shutdown.")
            return comment_status

        # The permalink/comment view is normally a centered modal. Click a
        # resolution-relative point on the dimmed backdrop to close it without
        # touching post actions, then linger briefly like a human operator.
        screen_h, screen_w = after_comment.shape[:2]
        backdrop_target = (
            max(20, int(screen_w * 0.20)),
            max(20, int(screen_h * 0.50)),
        )
        task.log("INFO", f"Closing the post modal from the outside backdrop at {backdrop_target}...")
        task.human.click(*backdrop_target)

        warm_seconds = random.uniform(1.0, 5.0)
        task.set_stage("warming", seconds=round(warm_seconds, 2))
        task.log("INFO", f"Post-comment warm-down for {warm_seconds:.1f}s...")
        time.sleep(warm_seconds)
        task.capture_evidence("after_comment_modal_close", task.client.screenshot())
        return comment_status

    def post_with_page_reuse(
        self,
        comment_link: str,
        *,
        caption: str | None,
        media_type: str,
    ) -> tuple[str, dict]:
        """Try the current profile feed before paying for navigation/correlation."""
        task = self.task
        comment_status = task.post_first_comment(
            comment_link,
            reuse_profile_page=True,
        )
        if comment_status != "failed_input_not_found":
            return comment_status, task.permalink_not_requested()

        task.log(
            "INFO",
            "Current profile feed did not expose the comment input; refreshing once to correlate the published post.",
        )
        permalink_info = task.correlate_and_extract_permalink(
            caption=caption,
            media_type=media_type,
        )
        if not permalink_info.get("post_url"):
            permalink_info.update({
                "permalink_status": "missing",
                "permalink_missing": True,
                "permalink_recovery_attempted": False,
            })
            return comment_status, permalink_info

        permalink_info = task.recover_missing_permalink(
            permalink_info,
            caption=caption,
            media_type=media_type,
        )
        comment_status = task.post_first_comment(
            comment_link,
            post_url=permalink_info["post_url"],
            prefer_permalink=True,
        )
        return comment_status, permalink_info

