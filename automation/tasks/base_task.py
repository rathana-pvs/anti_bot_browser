"""Base Task Class for Browser Automation."""

import re
import time
from datetime import datetime
from engine.container_client import ContainerClient
from engine.evidence import EvidenceRecorder
from engine.human_input import HumanInput
from engine.screen_state import FacebookStateRecognizer, ScreenState
from engine.vision import VisionEngine


class BaseTask:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.client = ContainerClient(profile_id)
        self.human = HumanInput(self.client)
        self.vision = VisionEngine(self.client)
        self.recognizer = FacebookStateRecognizer(self.vision)
        self.evidence = EvidenceRecorder(profile_id, self.__class__.__name__)
        self.logs: list[dict] = []
        self.result_status = "running"
        self.result_error: str | None = None

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
        try:
            self.evidence.write_result(status, error, **extra)
        except Exception as exc:
            self.log("WARN", f"Could not write evidence result: {exc}")
        return status in {"published", "completed"}

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
            time.sleep(poll_interval)
        return last

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
        self.client.navigate_to("https://www.facebook.com/")
        time.sleep(2.0)

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

        # Wait up to 6s for GTK dialog to appear
        win_id = None
        for _ in range(12):
            res = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", "Open Files"],
                check=False,
            )
            if res.returncode == 0:
                lines = res.stdout.strip().split()
                if lines:
                    win_id = lines[-1]
                    break
            time.sleep(0.5)

        if win_id:
            self.client.exec_cmd(["xdotool", "windowfocus", "--sync", str(win_id)], check=False)
            time.sleep(0.3)
        else:
            self.log("ERROR", "GTK 'Open Files' window was not detected; refusing blind input.")
            return False

        file_check = self.client.exec_cmd(["test", "-f", file_path], check=False)
        if file_check.returncode != 0:
            self.log("ERROR", f"Media file does not exist inside the container: {file_path}")
            return False

        # In GTK3 file chooser, Ctrl+L reliably activates the direct path text input
        self.human.key_press("ctrl+l")
        time.sleep(0.5)

        # Type the path inside the container as a direct argv argument
        self.client.exec_cmd(["xdotool", "type", "--", file_path], check=False)
        time.sleep(0.5)

        # Restrict OCR to the bottom portion of this dialog so browser text or
        # the Cancel button cannot be mistaken for the intended action.
        geometry = self.client.exec_cmd(
            ["xdotool", "getwindowgeometry", "--shell", str(win_id)],
            check=False,
        )
        values = dict(re.findall(r"^(X|Y|WIDTH|HEIGHT)=(\d+)$", geometry.stdout, re.MULTILINE))
        if len(values) != 4:
            self.log("ERROR", "Could not determine the file chooser geometry.")
            return False

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
        if not open_match:
            self.log("ERROR", "The visible Open button could not be located; refusing a blind confirmation.")
            return False

        self.log("INFO", f"Clicking the visually confirmed Open button at {open_match['center']}...")
        self.human.click(*open_match["center"])

        for _ in range(10):
            time.sleep(0.5)
            check_dialog = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", "Open Files"],
                check=False,
            )
            if check_dialog.returncode != 0 or not check_dialog.stdout.strip():
                break
        else:
            self.log("ERROR", "GTK dialog remained visible after clicking Open.")
            self.capture_evidence("file_chooser_open_failed")
            return False

        # Re-focus Chrome window
        chrome_res = self.client.exec_cmd(["xdotool", "search", "--class", "google-chrome"], check=False)
        if chrome_res.returncode == 0 and chrome_res.stdout.strip():
            chrome_win = chrome_res.stdout.strip().split()[-1]
            self.client.exec_cmd(["xdotool", "windowfocus", str(chrome_win)], check=False)

        return True

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
