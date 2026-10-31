"""Known dialogs: Chrome's Leave site prompt, Facebook's post-publish prompt,
and Meta's "Your Meta Account is here" announcement."""

import re
import time


class KnownDialogs:
    def __init__(self, task):
        self.task = task

    def leave_site(self, screen=None) -> bool:
        """
        Check if Chrome displayed the 'Leave site? Changes you made may not be saved.'
        modal dialog. If detected, click 'Leave' or send Return to dismiss it and unblock navigation.
        Returns True if a dialog was detected and dismissed, False otherwise.
        """
        task = self.task
        try:
            vision = getattr(task, "vision", None)
            if vision is None or not hasattr(vision, "find_leave_site_button"):
                return False
            coords = vision.find_leave_site_button(screen=screen)
            if coords:
                task.log("WARN", f"Detected 'Leave site?' navigation dialog. Clicking Leave at {coords}...")
                task.capture_evidence("dismiss_leave_site_dialog", screen=screen)
                human = getattr(task, "human", None)
                if human and hasattr(human, "click"):
                    human.click(coords[0], coords[1])
                elif getattr(task, "client", None) and hasattr(task.client, "dismiss_dialog_key"):
                    task.client.dismiss_dialog_key()
                time.sleep(1.0)
                return True
        except Exception as exc:
            task.log("DEBUG", f"Error during leave site dialog check: {exc}")
        return False

    def find_meta_account_notice(self, screen):
        """The announcement's title, when Meta's account notice covers the page."""
        height, width = screen.shape[:2]
        region = (int(width * .25), int(height * .25), int(width * .5), int(height * .5))
        for item in self.task.vision.read_text(screen, region=region, min_confidence=0.5):
            if "meta account is here" in re.sub(r"\s+", " ", item["text"].casefold()):
                return item
        return None

    def dismiss_meta_account_notice(self, screen=None) -> str:
        """Close Meta's account announcement; never press "See what's new".

        Escape closes it first. If it stays, the dialog's own X is clicked,
        found from the title's position. Returns dismissed, absent or failed.
        """
        task = self.task
        screen = task.client.screenshot() if screen is None else screen
        title = self.find_meta_account_notice(screen)
        if title is None:
            return "absent"
        task.log("INFO", "Meta's \"Your Meta Account is here\" notice is covering the page; closing it.")
        task.capture_evidence("meta_account_notice", screen)
        width = screen.shape[1]
        left, top, right, _ = title["bounds"]
        # The X sits at the dialog's top-right corner, about 35 px above the
        # title and 270 px right of its centre on a 1920 px wide screen.
        scale = width / 1920
        close_x, close_y = int((left + right) / 2 + 272 * scale), int(top - 35 * scale)
        for attempt in ("escape", "close_button"):
            if attempt == "escape":
                task.human.key_press("Escape")
            else:
                task.human.click(close_x, close_y)
            time.sleep(1.2)
            if self.find_meta_account_notice(task.client.screenshot()) is None:
                task.log("INFO", f"Closed Meta's account notice ({attempt.replace('_', ' ')}).")
                return "dismissed"
        task.log("WARN", "Meta's account notice stayed open after Escape and its close button.")
        return "failed"

    def find_post_publish_prompt(self, screen=None):
        """Return a modal-scoped Not now match when the known prompt is visible."""
        task = self.task
        if screen is None:
            screen = task.client.screenshot() if getattr(task, "client", None) else None
        if screen is None:
            return None
        match = None
        height, width = screen.shape[:2]
        prompt_region = (
            int(width * 0.15), int(height * 0.08),
            int(width * 0.70), int(height * 0.87),
        )
        for item in task.vision.read_text(screen, region=prompt_region, min_confidence=0.15):
            normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            compact = normalized.replace(" ", "")
            words = set(normalized.split())
            if compact in {"notnow", "notn0w"} or (
                "not" in words and ("now" in words or "n0w" in words)
            ):
                if match is None or item["confidence"] > match["confidence"]:
                    match = item
        return match

    def dismiss_post_prompt(self, screen=None) -> bool:
        """
        Detect and dismiss Facebook post-publish prompts (e.g. 'Speak With People Directly',
        'Add a button', WhatsApp prompts, or upsell modals) by clicking 'Not now'.
        """
        task = self.task
        try:
            match = task.find_post_publish_prompt(screen)
            if match:
                task.log("INFO", f"Detected post-publish prompt ('Not now'). Clicking at {match['center']}...")
                task.capture_evidence("dismiss_post_prompt", screen, target=list(match["center"]))
                task.click_reversible(
                    match["center"],
                    label="dismiss_post_prompt",
                    bounds=match.get("bounds"),
                    max_offset_px=4,
                )
                time.sleep(2.0)
                return True
        except Exception as exc:
            task.log("DEBUG", f"Error during post prompt check: {exc}")
        return False

    def handle_post_publish_prompt(self, screen=None) -> str:
        """Use the shared prompt handler, with compatibility for lightweight task doubles."""
        handler = getattr(self.task, "post_publish_prompt", None)
        if handler is not None:
            return handler.handle(screen)
        return "dismissed" if self.task.check_and_dismiss_post_prompt(screen) else "absent"
