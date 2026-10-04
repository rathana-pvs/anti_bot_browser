"""Template-independent publication result checks."""

from __future__ import annotations

import time
import re
from pathlib import Path


class ImagePublicationResultVerifier:
    """Check the posted image and its header, with one refresh on a miss."""

    def __init__(self, task, *, retry_delay: float = 15.0, max_scans: int = 8):
        self.task = task
        self.retry_delay = retry_delay
        self.max_scans = max_scans
        self._source = None

    def _matching_image_bounds(self, screen):
        import cv2
        import numpy as np

        if self._source is None:
            media = Path(self.task.media_path)
            shared = Path(__file__).resolve().parents[2] / "profiles" / "shared_media"
            if str(media).startswith("/data/shared_media/"):
                media = shared / media.relative_to("/data/shared_media")
            elif not media.is_absolute():
                media = shared / media
            self._source = cv2.imread(str(media))
        if self._source is None:
            return None
        source = self._source
        # Keep feature matching bounded while preserving the source aspect ratio.
        scale = min(1.0, 900.0 / max(source.shape[:2]))
        source = cv2.resize(source, None, fx=scale, fy=scale)
        height, width = screen.shape[:2]
        x0, y0 = int(width * 0.28), int(height * 0.16)
        crop = screen[y0:, x0:int(width * 0.90)]
        detector = cv2.ORB_create(nfeatures=2000)
        keys_a, desc_a = detector.detectAndCompute(source, None)
        keys_b, desc_b = detector.detectAndCompute(crop, None)
        if desc_a is None or desc_b is None:
            return None
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance]
        if len(good) < 10:
            return None
        points_a = np.float32([keys_a[m.queryIdx].pt for m in good])
        points_b = np.float32([keys_b[m.trainIdx].pt for m in good])
        transform, mask = cv2.findHomography(points_a, points_b, cv2.RANSAC, 4.0)
        if transform is None or mask is None or int(mask.sum()) < 8 or float(mask.mean()) < 0.5:
            return None
        sh, sw = source.shape[:2]
        corners = np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]]).reshape(-1, 1, 2)
        mapped = cv2.perspectiveTransform(corners, transform).reshape(-1, 2)
        if not np.isfinite(mapped).all() or not cv2.isContourConvex(mapped.astype(np.float32)):
            return None
        left, top = mapped.min(axis=0)
        right, bottom = mapped.max(axis=0)
        if right - left < 100 or bottom - top < 100 or right - left > width or bottom - top > height * 3:
            return None
        return (float(left + x0), float(top + y0), float(right + x0), float(bottom + y0))

    def _post_visible(self, screen):
        bounds = self._matching_image_bounds(screen)
        if not bounds:
            return False
        left, top, right, bottom = bounds
        height, width = screen.shape[:2]
        header_top = max(int(height * 0.16), int(top - 350))
        header = (max(0, int(left - 25)), header_top,
                  min(width, int(right + 25)) - max(0, int(left - 25)), max(1, int(top) - header_top))
        items = self.task.vision.read_text(screen, region=header, min_confidence=0.18)
        timestamps = [item for item in items if self.task._is_recent_timestamp_text(item.get("text", ""))]
        if not timestamps:
            return False
        caption = re.sub(r"[^a-z0-9]+", " ", (self.task.caption or "").casefold()).strip()
        # Do not accept caption text elsewhere on the page as a post match.
        if caption:
            words = set(caption.split())
            visible = set(re.sub(r"[^a-z0-9]+", " ", " ".join(item.get("text", "") for item in items).casefold()).split())
            if len(words & visible) < min(2, len(words)):
                return False
        self.task.capture_evidence("image_post_matched", screen, image_bounds=list(bounds),
                                   timestamp=timestamps[0].get("text"), caption_matched=bool(caption))
        return True

    def _scan(self):
        self.task.client.exec_cmd(["xdotool", "key", "ctrl+home"], check=False)
        time.sleep(0.8)
        self.task.client.exec_cmd(["xdotool", "mousemove", "1150", "500"], check=False)
        screen = None
        for scan in range(1, self.max_scans + 1):
            screen = self.task.client.screenshot()
            if self._post_visible(screen):
                return True, screen
            if scan < self.max_scans:
                self.task.human.scroll("down", notches=2)
                time.sleep(1.2)
        return False, screen

    def verify_latest_image(self):
        for attempt in range(1, 3):
            if attempt == 2:
                self.task.log("INFO", "Latest image post was not found; waiting before one profile refresh.")
                time.sleep(self.retry_delay)
                self.task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
            visible, screen = self._scan()
            self.task.capture_evidence(f"latest_image_check_{attempt}", screen, latest_image_visible=visible)
            if visible:
                return True
        return False


class ReelPublicationResultVerifier:
    """Verify a posted Reel on its profile without knowing its composer template."""

    def __init__(self, task, *, attempts: int = 2, retry_delay: float = 15.0):
        self.task = task
        self.attempts = attempts
        self.retry_delay = retry_delay

    def verify_latest_reel(self) -> bool:
        for attempt in range(1, self.attempts + 1):
            self.task.log(
                "INFO",
                f"Refreshing the profile to check for the latest Reel (attempt {attempt}/{self.attempts}).",
            )
            self.task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
            visible, screen = self.task._scan_profile_for_latest_reel()
            self.task.capture_evidence(
                f"latest_reel_check_{attempt}",
                screen,
                latest_reel_visible=visible,
            )
            if visible:
                return True
            if attempt < self.attempts:
                self.task.log(
                    "INFO",
                    f"Latest Reel is not visible yet; waiting {self.retry_delay:g} seconds before one final refresh.",
                )
                time.sleep(self.retry_delay)
        return False
