"""Attaching a media file through the native GTK file chooser (zero CDP).

On a phone this skill is replaced by a share intent; workflows only ask for a
file to be attached.
"""

import time

from engine.telemetry import timed_telemetry_step


class MediaAttachment:
    def __init__(self, task):
        self.task = task

    @property
    def telemetry(self):
        return getattr(self.task, "telemetry", None)

    def find_chooser(self) -> tuple[str, str] | None:
        """Return the visible GTK chooser window id and matched title pattern."""
        task = self.task
        for dialog_name in task.FILE_CHOOSER_DIALOG_NAMES:
            window_ids = task.client.find_windows(dialog_name)
            if window_ids:
                return window_ids[-1], dialog_name
        return None

    @timed_telemetry_step("media_upload")
    def attach(
        self,
        file_path: str,
        chooser_window: tuple[str, str] | None = None,
    ) -> bool:
        """
        Attach a file using the native Linux GTK file chooser dialog (Zero-CDP).
        Focuses the visible 'Open Files' window, enters the direct path, then
        visually locates and clicks its Open button. It never confirms with a
        blind Return press because focus can move to Cancel in GTK dialogs.
        """
        task = self.task
        task.log("STEP", f"Zero-CDP GTK file attachment: {file_path}")

        # Wait up to 15s for GTK dialog to appear. Reel Studio can delay opening
        # the native chooser while its upload UI initializes.
        detected_chooser = chooser_window
        for _ in range(30):
            if detected_chooser is None:
                detected_chooser = task.find_visible_file_chooser()
            if detected_chooser is not None:
                break
            time.sleep(0.5)

        if detected_chooser is not None:
            win_id, matched_dialog_name = detected_chooser
            task.client.focus_window(win_id)
            time.sleep(0.3)
        else:
            task.log("ERROR", "GTK file chooser window ('Open File') was not detected; refusing blind input.")
            task.capture_evidence(
                "file_chooser_not_detected",
                visible_window_ids=task.client.find_windows(".*"),
                searched_titles=list(task.FILE_CHOOSER_DIALOG_NAMES),
            )
            return False

        if not task.client.file_exists(file_path):
            task.log("ERROR", f"Media file does not exist inside the container: {file_path}")
            return False

        # In GTK3 file chooser, Ctrl+L reliably activates the direct path text input
        task.human.key_press("ctrl+l")
        time.sleep(0.3)
        task.human.key_press("ctrl+a")
        time.sleep(0.1)
        task.human.key_press("BackSpace")
        time.sleep(0.2)

        # Type the path inside the container as a direct argv argument
        task.human.type_native(file_path)
        time.sleep(0.5)

        # Locate and click the dialog's Open button to submit the selected file
        values = task.client.window_geometry(win_id)
        clicked_open = False
        if len(values) == 4:
            dialog_x = values["X"]
            dialog_y = values["Y"]
            dialog_w = values["WIDTH"]
            dialog_h = values["HEIGHT"]
            screen = task.client.screenshot()
            action_region = (
                dialog_x + dialog_w // 2,
                dialog_y + (dialog_h * 2) // 3,
                dialog_w // 2,
                dialog_h // 3,
            )
            open_match = task.vision.find_text(
                ("open", "select"),
                screen=screen,
                region=action_region,
                min_confidence=0.35,
            )
            task.capture_evidence(
                "file_chooser_ready",
                screen,
                file_dialog_window=win_id,
                action_region=list(action_region),
                open_target=list(open_match["center"]) if open_match else None,
            )
            if open_match:
                task.log("INFO", f"Clicking the visually confirmed Open button at {open_match['center']}...")
                task.human.click(*open_match["center"])
                clicked_open = True
            else:
                fallback_open = (dialog_x + dialog_w - 48, dialog_y + dialog_h - 22)
                task.log("INFO", f"Clicking geometric Open button at {fallback_open}...")
                task.human.click(*fallback_open)
                clicked_open = True

        if not clicked_open:
            task.human.key_press("Return", check=False)

        for _ in range(12):
            time.sleep(0.5)
            if not task.client.find_windows(matched_dialog_name or "Open File"):
                task.log("INFO", "GTK file chooser dismissed successfully.")
                break
        else:
            task.log("ERROR", "GTK dialog remained visible after submitting file.")
            task.capture_evidence("file_chooser_open_failed")
            return False

        # GTK and Chromium helpers also match the Chrome class. The last
        # search result can be a tiny helper, leaving X11 focus on the root
        # after the chooser closes. Restore the largest visible browser window
        # synchronously before returning to composer input.
        if task.client.ensure_focus() <= 0:
            task.log("ERROR", "Chrome window was not found after file attachment.")
            task.capture_evidence("file_chooser_browser_focus_failed")
            return False

        return True

