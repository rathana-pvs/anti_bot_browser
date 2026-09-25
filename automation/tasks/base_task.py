"""Base Task Class for Browser Automation."""

import random
import re
import time
import urllib.parse
import numpy as np
from datetime import datetime, timezone
from engine.container_client import ContainerClient
from engine.evidence import EvidenceRecorder
from engine.human_input import HumanInput
from engine.screen_state import FacebookStateRecognizer, ScreenState
from engine.vision import VisionEngine
from engine.semantic_fallback import SemanticFallbackEngine


class BaseTask:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.client = ContainerClient(profile_id)
        self.human = HumanInput(self.client)
        self.vision = VisionEngine(self.client)
        self.recognizer = FacebookStateRecognizer(self.vision)
        self.evidence = EvidenceRecorder(profile_id, self.__class__.__name__)
        self.semantic_fallback = SemanticFallbackEngine()
        self.logs: list[dict] = []
        self.result_status = "running"
        self.result_error: str | None = None
        self.result_extra: dict = {}
        self.current_stage: str = "pending"
        self.stage_history: list[dict] = []

    def set_stage(self, stage: str, **metadata) -> None:
        """Atomically transition execution stage and persist progress."""
        self.current_stage = stage
        entry = {
            "stage": stage,
            "timestamp": datetime.now().isoformat(),
            **metadata,
        }
        if not hasattr(self, "stage_history"):
            self.stage_history = []
        self.stage_history.append(entry)
        self.log("STAGE", f"Execution stage: {stage}")
        try:
            if hasattr(self, "evidence") and self.evidence:
                self.evidence.record_stage(stage, self.stage_history)
        except Exception as exc:
            self.log("WARN", f"Could not record stage '{stage}': {exc}")

    def log(self, level: str, message: str) -> None:
        """Record a structured log entry."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "profile_id": self.profile_id,
            "level": level.upper(),
            "message": message,
        }
        self.logs.append(entry)
        print(f"[{entry['timestamp']}] [{self.profile_id}] [{entry['level']}] {message}", flush=True)

    def log_decision(
        self,
        step: str,
        looking_for: str,
        found: str,
        decision: str,
        level: str = "STEP",
    ) -> None:
        """Write one compact, UI-friendly decision record on a single stream line."""
        message = (
            f"{step}:\\n"
            f"- what it is looking for: {looking_for}\\n"
            f"- what it found: {found}\\n"
            f"- decision: {decision}"
        )
        self.log(level, message)

    def capture_evidence(self, label: str, screen=None, **metadata) -> str | None:
        """Capture diagnostic evidence without allowing evidence I/O to abort a task."""
        try:
            screen = self.client.screenshot() if screen is None else screen
            path = self.evidence.capture(label, screen, metadata or None)
            self.log("DEBUG", f"Evidence captured: {path}")
            return path
        except Exception as exc:
            self.log("WARN", f"Could not capture evidence '{label}': {exc}")
            return None

    def set_outcome(self, status: str, error: str | None = None, **extra) -> bool:
        """Persist a final outcome and return whether it represents publication."""
        self.result_status = status
        self.result_error = error
        self.result_extra = extra
        self.set_stage(status, error=error, **extra)
        try:
            self.evidence.write_result(status, error, **extra)
        except Exception as exc:
            self.log("WARN", f"Could not write evidence result: {exc}")
        return status in {"published", "completed"}

    def rank_candidates_semantically(
        self,
        goal: str,
        screen: np.ndarray,
        region: str | tuple[int, int, int, int] | tuple[float, float, float, float] | None = None,
        is_reversible: bool = True,
        min_ocr_confidence: float = 0.20,
    ) -> tuple[bool, str, tuple[int, int] | None]:
        """
        Extract OCR candidates in the specified region and evaluate semantic fallback.
        Audits proposal via evidence recorder and enforces deterministic safety gates.
        Returns: (allow_click, status_reason, target_coords)
        """
        candidates = self.vision.read_text(screen, region=region, min_confidence=min_ocr_confidence)
        if not candidates:
            return False, "no_candidates", None

        obs = self.recognizer.observe(screen)
        state_name = obs.state.value

        proposal, candidate_map = self.semantic_fallback.rank_candidates(
            state=state_name,
            goal=goal,
            raw_candidates=candidates,
            expected_region=region,
        )

        if not proposal or not proposal.candidate_id:
            return False, "no_proposal", None

        allow_click, status_reason, target_coords = self.semantic_fallback.validate_proposal(
            proposal=proposal,
            candidate_map=candidate_map,
            current_screen=screen,
            expected_region=region,
            recognizer=self.recognizer,
            is_reversible=is_reversible,
        )

        # Audit fallback in evidence
        try:
            structured_candidates = [c.to_structured_dict() for c in candidate_map.values()]
            self.evidence.record_semantic_fallback(
                goal=goal,
                state=state_name,
                proposal=proposal.to_dict(),
                candidates=structured_candidates,
                screen=screen,
            )
        except Exception as exc:
            self.log("WARN", f"Could not record semantic fallback audit: {exc}")

        cand_text = candidate_map[proposal.candidate_id].text if proposal.candidate_id in candidate_map else "unknown"
        if proposal.shadow_mode:
            self.log(
                "INFO",
                f"[SHADOW MODE] Semantic fallback proposed '{cand_text}' ({proposal.candidate_id}, conf={proposal.confidence:.2f}) "
                f"for goal '{goal}'. Deterministic status: {status_reason}. No click executed."
            )
        else:
            self.log(
                "INFO" if allow_click else "WARN",
                f"Semantic fallback proposed '{cand_text}' (conf={proposal.confidence:.2f}) for goal '{goal}'. "
                f"Result: {status_reason} (allowed={allow_click})."
            )

        return allow_click, status_reason, target_coords

    def wait_for_states(
        self,
        expected: set[ScreenState],
        timeout: float,
        poll_interval: float = 1.0,
        stable_observations: int = 1,
    ):
        """Poll screenshots until an expected state is observed repeatedly."""
        deadline = time.time() + timeout
        previous_state = None
        stable_count = 0
        last = None
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            last = (observation, screen)
            if observation.state in expected:
                if observation.state == previous_state:
                    stable_count += 1
                else:
                    previous_state = observation.state
                    stable_count = 1
                if stable_count >= stable_observations:
                    return observation, screen
            else:
                previous_state = observation.state
                stable_count = 0
                if observation.state == ScreenState.UNKNOWN:
                    if self.handle_leave_site_dialog(screen):
                        time.sleep(poll_interval)
                        continue
            time.sleep(poll_interval)
        return last

    def handle_leave_site_dialog(self, screen=None) -> bool:
        """
        Check if Chrome displayed the 'Leave site? Changes you made may not be saved.'
        modal dialog. If detected, click 'Leave' or send Return to dismiss it and unblock navigation.
        Returns True if a dialog was detected and dismissed, False otherwise.
        """
        try:
            vision = getattr(self, "vision", None)
            if vision is None or not hasattr(vision, "find_leave_site_button"):
                return False
            coords = vision.find_leave_site_button(screen=screen)
            if coords:
                self.log("WARN", f"Detected 'Leave site?' navigation dialog. Clicking Leave at {coords}...")
                self.capture_evidence("dismiss_leave_site_dialog", screen=screen)
                human = getattr(self, "human", None)
                if human and hasattr(human, "click"):
                    human.click(coords[0], coords[1])
                elif getattr(self, "client", None) and hasattr(self.client, "dismiss_dialog_key"):
                    self.client.dismiss_dialog_key()
                time.sleep(1.0)
                return True
        except Exception as exc:
            self.log("DEBUG", f"Error during leave site dialog check: {exc}")
        return False

    def check_and_dismiss_post_prompt(self, screen=None) -> bool:
        """
        Detect and dismiss Facebook post-publish prompts (e.g. 'Speak With People Directly',
        'Add a button', WhatsApp prompts, or upsell modals) by clicking 'Not now'.
        """
        try:
            if screen is None:
                screen = self.client.screenshot() if hasattr(self, "client") and self.client else None
            if screen is None:
                return False

            # Read the current frame once and use token matching. The prompt can
            # appear only after Facebook finishes its Posting state, and OCR can
            # render "Not now" as one token or confuse the letter o with zero.
            match = None
            height, width = screen.shape[:2]
            prompt_region = (
                int(width * 0.15),
                int(height * 0.08),
                int(width * 0.70),
                int(height * 0.87),
            )
            for item in self.vision.read_text(
                screen,
                region=prompt_region,
                min_confidence=0.15,
            ):
                normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                compact = normalized.replace(" ", "")
                words = set(normalized.split())
                if compact in {"notnow", "notn0w"} or (
                    "not" in words and ("now" in words or "n0w" in words)
                ):
                    if match is None or item["confidence"] > match["confidence"]:
                        match = item
            if match:
                self.log("INFO", f"Detected post-publish prompt ('Not now'). Clicking at {match['center']}...")
                self.capture_evidence("dismiss_post_prompt", screen, target=list(match["center"]))
                self.human.click(*match["center"])
                time.sleep(2.0)
                return True
        except Exception as exc:
            self.log("DEBUG", f"Error during post prompt check: {exc}")
        return False

    def navigate_to(self, url: str, wait_seconds: float = 2.0) -> None:
        """
        Navigate to URL via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        if hasattr(self, "client") and self.client is not None:
            self.client.navigate_to(url)
        time.sleep(1.0)
        self.handle_leave_site_dialog()
        if wait_seconds > 1.0:
            time.sleep(wait_seconds - 1.0)

    def refresh_page(self, wait_seconds: float = 2.0) -> None:
        """
        Refresh active browser page via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        if hasattr(self, "client") and self.client is not None:
            self.client.refresh_page()
        time.sleep(1.0)
        self.handle_leave_site_dialog()
        if wait_seconds > 1.0:
            time.sleep(wait_seconds - 1.0)

    def paste_text(self, text: str) -> None:
        """
        Copy text (including full Unicode & emojis) to container X11 clipboard
        using xclip and paste it into the active element with Ctrl+V.
        """
        try:
            import subprocess
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "-u",
                    "chromeuser",
                    "-e",
                    f"DISPLAY={self.client.display}",
                    self.client.container_name,
                    "xclip",
                    "-selection",
                    "clipboard",
                ],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
            time.sleep(0.15)
            self.human.key_press("ctrl+v")
            time.sleep(0.2)
        except Exception as e:
            self.log("WARN", f"Clipboard paste failed, falling back to typing: {e}")
            self.human.type_text(text)

    def verify_logged_in(self) -> bool:
        """
        Verify that the container Chrome session is logged into Facebook.
        Loading and transitional screens remain UNKNOWN and are polled rather
        than being mistaken for a logged-out session.
        """
        self.log("INFO", "Verifying Facebook session login state...")
        self.navigate_to("https://www.facebook.com/", wait_seconds=2.0)

        # Check window title first because it is cheap and works without OCR.
        try:
            res = self.client.exec_cmd(["xdotool", "getactivewindow", "getwindowname"], check=False)
            if res.returncode == 0:
                title = res.stdout.strip().lower()
                if any(k in title for k in ("log in", "login", "sign up", "welcome to facebook")):
                    self.log("ERROR", f"Session expired or logged out (Window title: {title})")
                    return False
        except Exception:
            pass

        deadline = time.time() + 50.0
        observation = None
        screen = None
        valid_logged_in_states = {
            ScreenState.FEED_READY,
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_UPLOADING,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
            ScreenState.PUBLISHING,
            ScreenState.POST_CONFIRMED,
        }
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            if observation.state == ScreenState.LOGIN_REQUIRED:
                break
            if observation.state in valid_logged_in_states:
                break
            if observation.state == ScreenState.UNKNOWN:
                if self.handle_leave_site_dialog(screen):
                    time.sleep(1.0)
                    continue
            self.log("INFO", "Facebook is still loading or the screen is transitional; checking again...")
            time.sleep(2.0)

        if observation is None or screen is None:
            self.log("ERROR", "Facebook session verification produced no visual observation.")
            return False

        self.capture_evidence(
            "login_check",
            screen,
            state=observation.state.value,
            confidence=observation.confidence,
            signals=observation.signals,
        )
        if observation.state == ScreenState.LOGIN_REQUIRED:
            self.log("ERROR", "Facebook login screen was visually detected.")
            return False
        if observation.state not in valid_logged_in_states:
            self.log("ERROR", "Facebook session remained visually unverified after the loading timeout.")
            return False
        return True

    def attach_file_gtk(self, file_path: str) -> bool:
        """
        Attach a file using the native Linux GTK file chooser dialog (Zero-CDP).
        Focuses the visible 'Open Files' window, enters the direct path, then
        visually locates and clicks its Open button. It never confirms with a
        blind Return press because focus can move to Cancel in GTK dialogs.
        """
        self.log("STEP", f"Zero-CDP GTK file attachment: {file_path}")

        # Wait up to 15s for GTK dialog to appear. Reel Studio can delay opening
        # the native chooser while its upload UI initializes.
        win_id = None
        matched_dialog_name = None
        dialog_names = ("Open File", "File Upload", "Select a File", "Choose File", "^Open$")
        for _ in range(30):
            for dialog_name in dialog_names:
                res = self.client.exec_cmd(
                    ["xdotool", "search", "--onlyvisible", "--name", dialog_name],
                    check=False,
                )
                if res.returncode == 0:
                    lines = res.stdout.strip().split()
                    if lines:
                        win_id = lines[-1]
                        matched_dialog_name = dialog_name
                        break
            if win_id:
                break
            time.sleep(0.5)

        if win_id:
            self.client.exec_cmd(["xdotool", "windowfocus", "--sync", str(win_id)], check=False)
            time.sleep(0.3)
        else:
            self.log("ERROR", "GTK file chooser window ('Open File') was not detected; refusing blind input.")
            visible_windows = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", ".*"],
                check=False,
            )
            self.capture_evidence(
                "file_chooser_not_detected",
                visible_window_ids=visible_windows.stdout.strip().split(),
                searched_titles=list(dialog_names),
            )
            return False

        file_check = self.client.exec_cmd(["test", "-f", file_path], check=False)
        if file_check.returncode != 0:
            self.log("ERROR", f"Media file does not exist inside the container: {file_path}")
            return False

        # In GTK3 file chooser, Ctrl+L reliably activates the direct path text input
        self.human.key_press("ctrl+l")
        time.sleep(0.3)
        self.human.key_press("ctrl+a")
        time.sleep(0.1)
        self.human.key_press("BackSpace")
        time.sleep(0.2)

        # Type the path inside the container as a direct argv argument
        self.client.exec_cmd(["xdotool", "type", "--", file_path], check=False)
        time.sleep(0.5)

        # Locate and click the dialog's Open button to submit the selected file
        geometry = self.client.exec_cmd(
            ["xdotool", "getwindowgeometry", "--shell", str(win_id)],
            check=False,
        )
        values = dict(re.findall(r"^(X|Y|WIDTH|HEIGHT)=(\d+)$", geometry.stdout, re.MULTILINE))
        clicked_open = False
        if len(values) == 4:
            dialog_x = int(values["X"])
            dialog_y = int(values["Y"])
            dialog_w = int(values["WIDTH"])
            dialog_h = int(values["HEIGHT"])
            screen = self.client.screenshot()
            action_region = (
                dialog_x + dialog_w // 2,
                dialog_y + (dialog_h * 2) // 3,
                dialog_w // 2,
                dialog_h // 3,
            )
            open_match = self.vision.find_text(
                ("open", "select"),
                screen=screen,
                region=action_region,
                min_confidence=0.35,
            )
            self.capture_evidence(
                "file_chooser_ready",
                screen,
                file_dialog_window=win_id,
                action_region=list(action_region),
                open_target=list(open_match["center"]) if open_match else None,
            )
            if open_match:
                self.log("INFO", f"Clicking the visually confirmed Open button at {open_match['center']}...")
                self.human.click(*open_match["center"])
                clicked_open = True
            else:
                fallback_open = (dialog_x + dialog_w - 48, dialog_y + dialog_h - 22)
                self.log("INFO", f"Clicking geometric Open button at {fallback_open}...")
                self.human.click(*fallback_open)
                clicked_open = True

        if not clicked_open:
            self.client.exec_cmd(["xdotool", "key", "Return"], check=False)

        for _ in range(12):
            time.sleep(0.5)
            check_dialog = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", matched_dialog_name or "Open File"],
                check=False,
            )
            if check_dialog.returncode != 0 or not check_dialog.stdout.strip():
                self.log("INFO", "GTK file chooser dismissed successfully.")
                break
        else:
            self.log("ERROR", "GTK dialog remained visible after submitting file.")
            self.capture_evidence("file_chooser_open_failed")
            return False

        # Re-focus Chrome window
        chrome_res = self.client.exec_cmd(["xdotool", "search", "--class", "google-chrome"], check=False)
        if chrome_res.returncode == 0 and chrome_res.stdout.strip():
            chrome_win = chrome_res.stdout.strip().split()[-1]
            self.client.exec_cmd(["xdotool", "windowfocus", str(chrome_win)], check=False)

        return True

    def _find_first_comment_input(self, screen=None):
        """Locate the 'Comment as ...' or 'Write a comment...' input field."""
        if hasattr(self.vision, "find_comment_input"):
            res = self.vision.find_comment_input(screen=screen)
            if res is not None and type(res).__name__ not in ("Mock", "MagicMock"):
                return res
        # Fallback / mock support
        candidates = []
        screen = self.client.screenshot() if screen is None and hasattr(self, "client") and self.client else screen
        for item in self.vision.read_text(screen, min_confidence=0.15):
            normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            words = set(normalized.split())
            if normalized.startswith("comment as") or "comment as" in normalized or {"write", "comment"}.issubset(words):
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["center"][1])
        return candidates[0]["center"]

    def _open_profile_first_comment_input(self, post_url: str | None = None):
        """
        Locate the post card's 'Comment as ...' field:
        1. Check current screen first (e.g. if permalink modal or post is already open).
        2. If post_url provided, navigate to post_url directly.
        3. Otherwise navigate to profile feed and scroll down in the post stream.
        """
        # Step 1: Check current screen immediately (e.g. permalink view already on screen)
        current_screen = self.client.screenshot()
        target = self._find_first_comment_input(current_screen)
        if target:
            self.log("INFO", f"Found 'Comment as ...' directly on current screen at {target}")
            return target, current_screen

        # Step 2: Navigate directly to post_url if provided
        if post_url:
            self.log("STEP", f"Navigating to post URL to locate comment field: {post_url}")
            try:
                self.navigate_to(post_url, wait_seconds=3.0)
                post_screen = self.client.screenshot()
                target = self._find_first_comment_input(post_screen)
                if target:
                    return target, post_screen
            except Exception as exc:
                self.log("WARN", f"Could not navigate to post URL: {exc}")

        # Step 3: Navigate to profile and scroll down the post stream
        self.log("STEP", "Navigating to profile to locate post's 'Comment as ...' field...")
        self.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)

        # Move mouse over main post feed column (x ~ 1150, y ~ 500) so mouse wheel / keys scroll feed
        self.client.exec_cmd(["xdotool", "mousemove", "1150", "500"], check=False)

        for scan in range(1, 7):
            screen = self.client.screenshot()
            target = self._find_first_comment_input(screen)
            self.log_decision(
                "Find 'Comment as ...' field",
                "post card comment input ('Comment as ...' or 'Write a comment...')",
                f"scan={scan}, target={target}",
                "click comment field" if target else "scroll and scan again",
                level="INFO",
            )
            if target:
                return target, screen

            # If comment input not expanded, check for 'Comment' button under post card
            action_btn = self.vision.find_text(
                ("comment",),
                screen=screen,
                region=(max(0, int(screen.shape[1] * 0.28)), 0, min(screen.shape[1] - int(screen.shape[1] * 0.28), 1050), screen.shape[0]),
                min_confidence=0.45,
            )
            if action_btn and action_btn["text"].casefold().strip() == "comment":
                self.log("INFO", f"Clicking 'Comment' action at {action_btn['center']} to expand input...")
                self.human.click(*action_btn["center"])
                time.sleep(1.5)
                screen = self.client.screenshot()
                target = self._find_first_comment_input(screen)
                if target:
                    return target, screen

            # Scroll down the feed using Page_Down
            self.client.exec_cmd(["xdotool", "key", "Page_Down"], check=False)
            time.sleep(1.2)

        return None, None

    def post_first_comment(self, comment_link: str, post_url: str | None = None) -> str:
        """
        Locate the post's 'Comment as ...' field, click it, paste comment_link, and submit once with Return.
        Returns: 'submitted_unverified' on submission, or 'failed_input_not_found'.
        """
        self.set_stage("commenting")
        self.log("STEP", "Locating 'Comment as ...' field for first comment...")
        time.sleep(1.5)
        comment_box_pos, comment_screen = self._open_profile_first_comment_input(post_url=post_url)
        if not comment_box_pos:
            self.log_decision(
                "Submit first comment",
                "'Comment as ...' field under post",
                "no verified 'Comment as' field found after scanning",
                "keep content published and skip comment",
                level="WARN",
            )
            return "failed_input_not_found"

        if comment_screen is None:
            comment_screen = self.client.screenshot()

        self.capture_evidence("before_first_comment", comment_screen, target=list(comment_box_pos))
        self.log_decision(
            "Submit first comment",
            "'Comment as ...' field under post",
            f"target={comment_box_pos}",
            "click 'Comment as ...', paste configured comment, and submit once",
        )
        self.log("STEP", f"Clicking 'Comment as ...' at {comment_box_pos}...")
        self.human.click(*comment_box_pos)
        time.sleep(1.0)
        self.paste_text(comment_link)
        time.sleep(0.5)
        self.human.key_press("Return")
        self.log("INFO", "First comment was submitted once; waiting for Facebook's Posting indicator to clear.")

        after_comment = None
        clear_streak = 0
        for _ in range(30):
            time.sleep(2.0)
            after_comment = self.client.screenshot()
            screen_h, screen_w = after_comment.shape[:2]
            comment_region = (
                int(screen_w * 0.28),
                int(screen_h * 0.52),
                int(screen_w * 0.45),
                int(screen_h * 0.46),
            )
            comment_text = " ".join(
                item["text"].casefold()
                for item in self.vision.read_text(
                    after_comment,
                    region=comment_region,
                    min_confidence=0.15,
                )
            )
            submission_pending = any(
                marker in comment_text
                for marker in ("posting", "sending", "submitting")
            )
            if submission_pending:
                clear_streak = 0
                continue
            clear_streak += 1
            if clear_streak >= 2:
                break

        if after_comment is None:
            after_comment = self.client.screenshot()
        self.capture_evidence("after_first_comment", after_comment)

        if clear_streak < 2:
            self.log("WARN", "First comment is still submitting; leaving the post modal open and not retrying.")
            return "submission_pending"

        self.log("SUCCESS", "First comment submission settled; no automatic retry will be attempted.")

        # The permalink/comment view is normally a centered modal. Click a
        # resolution-relative point on the dimmed backdrop to close it without
        # touching post actions, then linger briefly like a human operator.
        screen_h, screen_w = after_comment.shape[:2]
        backdrop_target = (
            max(20, int(screen_w * 0.20)),
            max(20, int(screen_h * 0.50)),
        )
        self.log("INFO", f"Closing the post modal from the outside backdrop at {backdrop_target}...")
        self.human.click(*backdrop_target)

        warm_seconds = random.uniform(1.0, 5.0)
        self.set_stage("warming", seconds=round(warm_seconds, 2))
        self.log("INFO", f"Post-comment warm-down for {warm_seconds:.1f}s...")
        time.sleep(warm_seconds)
        self.capture_evidence("after_comment_modal_close", self.client.screenshot())
        return "submitted_unverified"

    @staticmethod
    def validate_facebook_permalink(url: str, post_type: str = "post") -> tuple[bool, str | None]:
        """
        Validate that the URL belongs to an allowed Facebook host and has a
        legitimate permalink path shape (posts, reels, permalink.php, watch, etc.).
        Returns (is_valid, normalized_clean_url).
        """
        if not url or not isinstance(url, str):
            return False, None
        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False, None

        if parsed.scheme not in ("http", "https"):
            return False, None

        host = (parsed.netloc or "").lower().split(":")[0]
        allowed_hosts = {
            "facebook.com",
            "www.facebook.com",
            "m.facebook.com",
            "web.facebook.com",
            "mbasic.facebook.com",
        }
        if host not in allowed_hosts and not any(host.endswith("." + h) for h in ("facebook.com",)):
            return False, None

        path = parsed.path or ""
        query = parsed.query or ""

        # Reject naked home/profile/login pages
        if path in ("", "/", "/me", "/home.php", "/login.php", "/checkpoint"):
            return False, None
        if re.match(r"^/profile\.php$", path):
            q_params = urllib.parse.parse_qs(query)
            if not ("story_fbid" in q_params or "fbid" in q_params):
                return False, None

        is_reel_pattern = (
            bool(re.search(r"^/reel/\d+", path))
            or bool(re.search(r"^/[^/]+/videos/\d+", path))
            or (path.startswith("/watch") and "v=" in query)
        )

        is_post_pattern = (
            bool(re.search(r"^/[^/]+/posts/[a-zA-Z0-9_-]+", path))
            or (path == "/permalink.php" and "story_fbid=" in query)
            or (path.startswith("/photo") and "fbid=" in query)
            or (path.startswith("/story.php") and "story_fbid=" in query)
            or bool(re.search(r"^/groups/[^/]+/permalink/\d+", path))
            or is_reel_pattern
        )

        valid = is_reel_pattern if post_type == "reel" else is_post_pattern
        if not valid:
            return False, None

        # Clean tracking query parameters while preserving story IDs
        keep_params = {"story_fbid", "id", "fbid", "v", "set"}
        q_dict = urllib.parse.parse_qs(query)
        clean_q = {k: v for k, v in q_dict.items() if k in keep_params}
        clean_query_str = urllib.parse.urlencode(clean_q, doseq=True) if clean_q else ""

        clean_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            clean_query_str,
            "",
        ))
        return True, clean_url

    def correlate_and_extract_permalink(
        self,
        caption: str | None = None,
        media_type: str = "post",
        max_scans: int = 4,
    ) -> dict:
        """
        Navigate to profile, locate newly created post card via multi-signal correlation
        (caption tokens, recent timestamp, author), click the timestamp, and extract the URL.
        Returns a dict: { "post_url": str | None, "post_url_verified_at": str | None, "post_match_confidence": float }
        """
        empty_res = {
            "post_url": None,
            "post_url_verified_at": None,
            "post_match_confidence": 0.0,
        }

        self.log("STEP", "Navigating to profile to correlate published post and extract permalink...")
        try:
            self.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
        except Exception as exc:
            self.log("WARN", f"Could not navigate to profile for permalink correlation: {exc}")
            return empty_res

        # Tokenize caption for correlation if provided
        search_tokens = set()
        if caption:
            normalized_cap = re.sub(r"[^a-z0-9]+", " ", caption.casefold()).strip()
            all_tokens = [w for w in normalized_cap.split() if len(w) >= 3]
            search_tokens = set(all_tokens[:8])

        recent_timestamp_indicators = {"just now", "1m", "2m", "3m", "a few seconds ago", "moment ago"}

        for scan in range(1, max_scans + 1):
            screen = self.client.screenshot()
            ocr_items = self.vision.read_text(screen, min_confidence=0.18)

            caption_match_center = None
            timestamp_match_center = None
            matched_tokens_count = 0

            # Scan visible text items
            for item in ocr_items:
                text_clean = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                item_tokens = set(text_clean.split())

                # 1. Caption matching
                if search_tokens:
                    overlap = search_tokens.intersection(item_tokens)
                    if len(overlap) > matched_tokens_count and len(overlap) >= min(2, len(search_tokens)):
                        matched_tokens_count = len(overlap)
                        caption_match_center = item["center"]

                # 2. Timestamp matching
                is_timestamp = (
                    text_clean in recent_timestamp_indicators
                    or any(ind in text_clean for ind in ("just now", "few seconds", "1 min", "2 min"))
                    or bool(re.match(r"^\d+\s*(m|min|s)$", text_clean))
                )
                if is_timestamp and not timestamp_match_center:
                    timestamp_match_center = item["center"]

            # If caption was matched, scan specifically in the header region above the caption for timestamp
            if caption_match_center and not timestamp_match_center:
                header_region = (
                    max(0, caption_match_center[0] - 220),
                    max(0, caption_match_center[1] - 80),
                    400,
                    70,
                )
                header_items = self.vision.read_text(screen, region=header_region, min_confidence=0.18)
                for h_item in header_items:
                    h_clean = re.sub(r"[^a-z0-9]+", " ", h_item["text"].casefold()).strip()
                    if (
                        h_clean in recent_timestamp_indicators
                        or any(ind in h_clean for ind in ("just now", "few seconds", "1 min", "2 min", "second", "moment"))
                        or bool(re.match(r"^\d+\s*(m|min|s)$", h_clean))
                    ):
                        timestamp_match_center = h_item["center"]
                        break

            # Compute correlation confidence
            confidence = 0.0
            if search_tokens and matched_tokens_count > 0:
                fraction = matched_tokens_count / max(1, len(search_tokens))
                confidence += 0.50 * min(1.0, fraction * 1.5)
            elif not search_tokens:
                confidence += 0.40

            if timestamp_match_center:
                confidence += 0.40

            self.log_decision(
                "Correlate published post",
                f"caption tokens={search_tokens or 'none'}, recent timestamp",
                f"scan={scan}, matched_tokens={matched_tokens_count}, ts_target={timestamp_match_center}, confidence={confidence:.2f}",
                "click timestamp link" if (confidence >= 0.50 and timestamp_match_center) else "scroll to next section",
                level="INFO",
            )

            # If we have a confident match with a clickable timestamp:
            if confidence >= 0.50 and timestamp_match_center:
                self.capture_evidence("before_permalink_click", screen, target=list(timestamp_match_center), confidence=round(confidence, 2))
                self.human.click(*timestamp_match_center)
                time.sleep(2.5)

                raw_url = self.client.get_current_url()
                is_valid, clean_url = self.validate_facebook_permalink(raw_url or "", post_type=media_type)

                after_click_screen = self.client.screenshot()
                self.capture_evidence(
                    "permalink_extraction",
                    after_click_screen,
                    raw_url=raw_url,
                    clean_url=clean_url,
                    is_valid=is_valid,
                    confidence=round(confidence, 2),
                )

                if is_valid and clean_url:
                    self.log("SUCCESS", f"Captured validated Facebook permalink: {clean_url} (confidence={confidence:.2f})")
                    return {
                        "post_url": clean_url,
                        "post_url_verified_at": datetime.now(timezone.utc).isoformat(),
                        "post_match_confidence": round(confidence, 2),
                    }
                else:
                    self.log("WARN", f"Clicked timestamp, but resulting URL was not a valid permalink: {raw_url}")

            # If not confident or timestamp not clickable, scroll down and scan next cards
            self.human.scroll("down", notches=3)
            time.sleep(1.2)

        self.log("WARN", "Permalink correlation was ambiguous or not found; leaving post_url null.")
        self.capture_evidence("permalink_correlation_ambiguous")
        return empty_res

    def run_with_retry(self, max_retries: int = 2) -> bool:
        """Run task with retry logic and exponential backoff."""
        for attempt in range(1, max_retries + 2):
            try:
                self.log("INFO", f"Task execution attempt {attempt}/{max_retries + 1}...")
                success = self.run()
                if success:
                    return True
                self.log("WARN", f"Attempt {attempt} returned failure.")
            except Exception as e:
                self.log("ERROR", f"Attempt {attempt} raised exception: {e}")

            if attempt <= max_retries:
                wait_sec = 10 * attempt
                self.log("INFO", f"Backing off {wait_sec}s before retry...")
                time.sleep(wait_sec)

        self.log("ERROR", f"Task failed after {max_retries + 1} attempts.")
        return False

    def run(self) -> bool:
        """Execute the task. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement run()")
