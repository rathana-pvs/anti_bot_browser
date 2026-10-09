"""Container Client for Isolated Browser Automation.

Executes X11 mouse/keyboard commands and captures virtual display screenshots
directly from the Docker container via pure OS-level events (zero CDP).
"""

import subprocess
import re
import time
import uuid
import numpy as np
import cv2


class ContainerClient:
    def __init__(self, profile_id: str, display: str = ":99"):
        self.profile_id = profile_id
        self.container_name = (
            profile_id if profile_id.startswith("isolated_") else f"isolated_{profile_id}"
        )
        self.display = display

    def is_running(self) -> bool:
        """Check if target container is currently active."""
        try:
            res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.stdout.strip() == "true"
        except Exception:
            return False

    def exec_cmd(self, cmd_args: list[str], check: bool = True, user: str = "chromeuser") -> subprocess.CompletedProcess:
        """Execute a command directly inside the container as user with DISPLAY set."""
        full_cmd = [
            "docker",
            "exec",
            "-u",
            user,
            "-e",
            f"DISPLAY={self.display}",
            self.container_name,
        ] + cmd_args
        return subprocess.run(full_cmd, capture_output=True, text=True, check=check)

    def get_chrome_window(self) -> int:
        """Find the active Chrome browser window ID."""
        res = self.exec_cmd(
            ["xdotool", "search", "--onlyvisible", "--class", "google-chrome|chrome|isolated-.*"],
            check=False,
        )
        if res.returncode != 0 or not res.stdout.strip():
            # Fallback search without --onlyvisible in case window manager hasn't mapped visibility flags
            res = self.exec_cmd(
                ["xdotool", "search", "--class", "google-chrome|chrome|isolated-.*"],
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return 0
        windows = [int(w.strip()) for w in res.stdout.strip().splitlines() if w.strip().isdigit()]
        if not windows:
            return 0
        # Return the window with the largest geometry (the main browser viewport)
        best_win = 0
        max_area = -1
        for w in windows:
            geo = self.exec_cmd(["xdotool", "getwindowgeometry", str(w)], check=False)
            if geo.returncode != 0:
                continue
            match = re.search(r"Geometry:\s*(\d+)x(\d+)", geo.stdout)
            if match:
                area = int(match.group(1)) * int(match.group(2))
                if area > max_area:
                    max_area = area
                    best_win = w
        return best_win

    def ensure_focus(self) -> int:
        """Ensure the main Chrome window has X11 input focus."""
        win_id = self.get_chrome_window()
        if win_id > 0:
            self.exec_cmd(["xdotool", "windowfocus", "--sync", str(win_id)])
        return win_id

    def focus_address_bar(self) -> bool:
        """Focus Chrome's address field with a real pointer click.

        The lightweight container window manager does not advertise
        ``_NET_ACTIVE_WINDOW`` and Chrome can ignore synthetic Ctrl+L events.
        A click at the address-field center is stable across the supported
        desktop resolutions and uses the same XTEST input path as automation.
        """
        win_id = self.ensure_focus()
        if win_id <= 0:
            return False
        geometry = self.exec_cmd(
            ["xdotool", "getwindowgeometry", "--shell", str(win_id)],
            check=False,
        )
        values = dict(
            re.findall(r"^(X|Y|WIDTH|HEIGHT)=(\-?\d+)$", geometry.stdout, re.MULTILINE)
        )
        if "WIDTH" not in values or "HEIGHT" not in values:
            return False
        address_x = max(1, int(values["WIDTH"]) // 2)
        address_y = min(61, max(1, int(values["HEIGHT"]) - 1))
        self.exec_cmd(
            [
                "xdotool",
                "mousemove",
                "--window",
                str(win_id),
                str(address_x),
                str(address_y),
            ]
        )
        self.exec_cmd(["xdotool", "click", "1"])
        time.sleep(0.1)
        return True

    def navigate_to(self, url: str) -> None:
        """Navigate through Chrome's address bar using OS-level input."""
        clean_modifiers = ["--clearmodifiers"]
        if not self.focus_address_bar():
            self.exec_cmd(["xdotool", "key"] + clean_modifiers + ["ctrl+l"])
            time.sleep(0.2)
        self.exec_cmd(["xdotool", "key"] + clean_modifiers + ["ctrl+a"])
        time.sleep(0.1)
        self.exec_cmd(["xdotool", "type"] + clean_modifiers + [url])
        time.sleep(0.1)
        self.exec_cmd(["xdotool", "key"] + clean_modifiers + ["Return"])

    def refresh_page(self) -> None:
        """Refresh the active browser tab via F5 keystroke."""
        win_id = self.ensure_focus()
        target_flag = ["--window", str(win_id)] if win_id > 0 else []
        self.exec_cmd(["xdotool", "key"] + target_flag + ["F5"])

    def dismiss_dialog_key(self) -> None:
        """Send Return key to confirm default action (e.g. 'Leave' in Leave site dialog)."""
        win_id = self.ensure_focus()
        target_flag = ["--window", str(win_id)] if win_id > 0 else []
        self.exec_cmd(["xdotool", "key"] + target_flag + ["Return"])

    def get_current_url(self) -> str | None:
        """
        Read the active URL from Chrome's address bar via OS-level clipboard events.
        Pure OS-level event, zero CDP required.
        """
        try:
            clean_modifiers = ["--clearmodifiers"]
            if not self.focus_address_bar():
                self.exec_cmd(["xdotool", "key"] + clean_modifiers + ["ctrl+l"])
                time.sleep(0.2)
            script = (
                f"xdotool key --clearmodifiers ctrl+a && "
                f"xdotool key --clearmodifiers ctrl+c && "
                f"sleep 0.15 && "
                f"xdotool key --clearmodifiers Escape && "
                f"timeout 3s xclip -o -selection clipboard"
            )
            res = self.exec_cmd(["bash", "-c", script], check=False)
            url = res.stdout.strip()
            if url.startswith("http://") or url.startswith("https://"):
                return url
            return None
        except Exception:
            return None

    def xdo(self, command: str) -> None:
        """Execute an xdotool command inside the container."""
        cmd_args = ["xdotool"] + command.split()
        self.exec_cmd(cmd_args, check=True)

    def batch_xdo(self, commands: list[str]) -> None:
        """Execute multiple xdotool commands efficiently in a single exec call."""
        if not commands:
            return
        script = " && ".join([f"xdotool {cmd}" for cmd in commands])
        subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"DISPLAY={self.display}",
                self.container_name,
                "bash",
                "-c",
                script,
            ],
            check=True,
            capture_output=True,
        )

    def get_mouse_position(self) -> tuple[int, int]:
        """Query current mouse coordinates (x, y) on the virtual display."""
        res = self.exec_cmd(["xdotool", "getmouselocation"])
        out = res.stdout.strip()
        # Format: x:500 y:500 screen:0 window:12582914
        match = re.search(r"x:(\d+)\s+y:(\d+)", out)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 0, 0

    def get_screen_dimensions(self) -> tuple[int, int]:
        """Query the virtual screen resolution (width, height)."""
        res = self.exec_cmd(["xdotool", "getdisplaygeometry"])
        out = res.stdout.strip().split()
        if len(out) >= 2:
            return int(out[0]), int(out[1])
        return 1920, 1080

    def screenshot(self) -> np.ndarray:
        """
        Capture current virtual display screen using scrot.
        Returns the image as an OpenCV BGR numpy array directly in memory.
        """
        # Every caller needs its own file: a viewer/diagnostic capture can run
        # while automation is reading its PNG. A shared path allows partial reads.
        tmp_path = f"/tmp/screen_{uuid.uuid4().hex}.png"
        try:
            self.exec_cmd(["scrot", "-o", tmp_path], user="root")
            proc = subprocess.run(
                ["docker", "exec", "-u", "root", self.container_name, "cat", tmp_path],
                capture_output=True,
                check=True,
            )
            nparr = np.frombuffer(proc.stdout, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError(f"Failed to decode screenshot from {self.container_name}")
            return img
        finally:
            # Screens may contain credentials; remove the transient capture even
            # when capture or decoding fails. Cleanup must not hide the error.
            try:
                self.exec_cmd(["rm", "-f", tmp_path], check=False, user="root")
            except Exception:
                pass

    def save_screenshot(self, host_output_path: str) -> None:
        """Capture screenshot and write directly to a host file."""
        img = self.screenshot()
        cv2.imwrite(host_output_path, img)
