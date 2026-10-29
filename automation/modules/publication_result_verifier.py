"""Template-independent publication result checks."""

from __future__ import annotations

import time
import re
from pathlib import Path

from engine.runtime_paths import profiles_dir
from .post_timestamp import parse_header_timestamp
from .reel_top_post import first_header_region, retry_unreadable_header


class ImagePublicationResultVerifier:
    """Check the newest feed card: recent timestamp plus caption OR media, then one retry."""

    def __init__(self, task, *, retry_delay: float = 15.0, max_scans: int = 8):
        self.task = task
        self.retry_delay = retry_delay
        self.max_scans = max_scans
        self._source = None
        self.last_check = {}
        self._top_post_found = False
        self._toolbar_seen = False

    def _matching_image_bounds(self, screen, media_path=None):
        import cv2
        import numpy as np

        if self._source is None:
            media = Path(media_path if media_path is not None else self.task.media_path)
            shared = profiles_dir() / "shared_media"
            if str(media).startswith("/data/shared_media/"):
                media = shared / media.relative_to("/data/shared_media")
            elif not media.is_absolute():
                media = shared / media
            if media.suffix.lower() in {".mp4", ".mov", ".webm"}:
                # Studio videos have a rendered cover at four seconds. Decode
                # that same frame for identity checks; never pass an MP4 to imread.
                video = cv2.VideoCapture(str(media))
                try:
                    video.set(cv2.CAP_PROP_POS_MSEC, 4000)
                    ok, frame = video.read()
                    if not ok:
                        video.set(cv2.CAP_PROP_POS_MSEC, 0)
                        ok, frame = video.read()
                    self._source = frame if ok else None
                finally:
                    video.release()
            else:
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

    def _record_check(self, **details):
        self.last_check = details
        self.task.log("INFO", f"Newest image post check: {details}")

    def _post_visible(self, screen):
        height, width = screen.shape[:2]
        items = self.task.vision.read_text(screen, min_confidence=0.18)
        region, seen = first_header_region(items, width, height)
        self._toolbar_seen = self._toolbar_seen or seen
        if region is None:
            self._record_check(reason="first_post_not_visible", toolbar_seen=self._toolbar_seen)
            return False
        self._top_post_found = True
        x, y, rw, rh = region
        # Full-screen OCR is downscaled to 1280px and loses the small grey
        # timestamp. Read this narrow header again at native resolution.
        header_items = self.task.vision.read_text(screen, region=region, min_confidence=0.18)
        header = [i for i in header_items if i.get("bounds")
                  and x <= i["bounds"][0] < x + rw
                  and y <= i["bounds"][1] and i["bounds"][3] <= y + rh]
        header, timestamp_check = retry_unreadable_header(
            self.task.vision, screen, region, header, min_confidence=.35)
        timestamp_reads = [i.get("text", "") for i in header
                           if parse_header_timestamp(i.get("text", "")) is not None]
        details = dict(header_reads=[i.get("text", "") for i in header],
                       timestamp_reads=timestamp_reads, timestamp_check=timestamp_check,
                       caption_matched=False, image_matched=False)
        if not timestamp_check["accepted"]:
            self._record_check(reason=timestamp_check["reason"], **details)
            return False

        # Bound identity checks to this card. Never borrow a caption, image or
        # recent timestamp from a later post or the profile's Photos sidebar.
        stamp_rows = [i for i in header if parse_header_timestamp(i.get("text", "")) is not None]
        first_stamp_y = min(i["bounds"][1] for i in stamp_rows)
        content_top = max(i["bounds"][3] for i in stamp_rows
                          if abs(i["bounds"][1] - first_stamp_y) < height * .015)
        card_bottom = height
        for item in items:
            bounds = item.get("bounds")
            if not bounds or not x <= bounds[0] < x + rw or bounds[1] < content_top:
                continue
            label = item.get("text", "").casefold().strip()
            if (label in {"see insights and ads", "like", "comment", "share", "boost post", "create ad"}
                    or parse_header_timestamp(label) is not None):
                card_bottom = min(card_bottom, bounds[1])
        caption_items = [i for i in items if i.get("bounds")
                         and x <= i["bounds"][0] < x + rw
                         and content_top <= i["bounds"][1] < min(card_bottom, content_top + 300)]
        words = set(re.findall(r"[^\W_]+", (self.task.caption or "").casefold()))
        visible = set(re.findall(r"[^\W_]+", " ".join(i.get("text", "") for i in caption_items).casefold()))
        caption_matched = bool(words) and len(words & visible) >= min(2, len(words))
        image_bounds = None
        if not caption_matched:
            card_screen = screen.copy()
            card_screen[:content_top] = 0
            card_screen[card_bottom:] = 0
            card_screen[:, :x] = 0
            card_screen[:, x + rw:] = 0
            image_bounds = self._matching_image_bounds(card_screen)
        matched = caption_matched or image_bounds is not None
        details.update(caption_matched=caption_matched, image_matched=image_bounds is not None)
        self._record_check(reason="recent_matching_first_post" if matched else "first_post_content_not_matched",
                           **details)
        self.task.capture_evidence("image_post_check", screen, **self.last_check,
                                   image_bounds=list(image_bounds) if image_bounds is not None else None)
        return matched

    def _scan(self):
        self._top_post_found = False
        self._toolbar_seen = False
        self.task.client.exec_cmd(["xdotool", "key", "ctrl+home"], check=False)
        time.sleep(0.8)
        self.task.client.exec_cmd(["xdotool", "mousemove", "1150", "500"], check=False)
        screen = None
        for scan in range(1, self.max_scans + 1):
            screen = self.task.client.screenshot()
            if self._post_visible(screen):
                return True, screen
            if self._top_post_found:
                return False, screen
            if scan < self.max_scans:
                self.task.human.scroll("down", notches=1 if self._toolbar_seen else 2)
                time.sleep(1.2)
        return False, screen

    def verify_latest_image(self):
        for attempt in range(1, 3):
            if attempt == 2:
                self.task.log("INFO", "Latest image post was not found; waiting before one profile refresh.")
                time.sleep(self.retry_delay)
                self.task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
            visible, screen = self._scan()
            self.task.capture_evidence(f"latest_image_check_{attempt}", screen, latest_image_visible=visible,
                                       verification=self.last_check)
            if visible:
                return True
        return False


class ReelPublicationResultVerifier:
    """Check the profile once, then refresh up to twice after unconfirmed reads."""

    def __init__(self, task, *, attempts: int = 3):
        self.task = task
        self.attempts = attempts

    def _poll_old_first_post(self, screen):
        """A readable old card can be stale content while Facebook updates it."""
        deadline = time.monotonic() + 15.
        self.task.log('INFO', 'The first post is old; polling this card for up to 15 seconds before refreshing.')
        while time.monotonic() < deadline:
            time.sleep(min(2., max(0., deadline - time.monotonic())))
            screen = self.task.client.screenshot()
            if self.task._latest_reel_is_visible(screen):
                return True, screen
            check = getattr(self.task, 'latest_reel_timestamp_check', None) or {}
            if check.get('reason') not in {'old_top_post', 'profile_feed_loading',
                                          'top_header_unreadable', 'top_timestamp_unreadable'}:
                break
        return False, screen

    def verify_latest_reel(self) -> bool:
        for attempt in range(1, self.attempts + 1):
            if not self.task._allow_reel_profile_navigation():
                return False
            self.task.log(
                "INFO",
                f"{'Opening' if attempt == 1 else 'Refreshing'} the profile to check for the latest Reel (attempt {attempt}/{self.attempts}).",
            )
            self.task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
            if not self.task._wait_for_reel_profile_ready():
                continue
            visible, screen = self.task._scan_profile_for_latest_reel()
            if not visible and (getattr(self.task, 'latest_reel_timestamp_check', None) or {}).get('reason') == 'old_top_post':
                visible, screen = self._poll_old_first_post(screen)
            self.task.capture_evidence(
                f"latest_reel_check_{attempt}",
                screen,
                latest_reel_visible=visible,
                timestamp_check=getattr(self.task, 'latest_reel_timestamp_check', None),
                readiness=getattr(self.task, 'reel_profile_readiness', None),
            )
            if visible:
                return True
            if attempt < self.attempts:
                self.task.log(
                    "INFO",
                    "Latest Reel is still unconfirmed after the profile check; refreshing and repeating the loading wait and timestamp read.",
                )
        # A gallery fallback is only safe after a fully settled profile scan.
        readiness = getattr(self.task, 'reel_profile_readiness', None) or {}
        if readiness.get('accepted') is True:
            return self.task._verify_reel_gallery()
        return False
