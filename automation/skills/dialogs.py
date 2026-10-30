"""Known dialogs: Chrome's Leave site prompt and Facebook's post-publish prompt."""

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
