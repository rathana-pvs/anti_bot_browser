"""Permalinks for published posts: validate Facebook post URLs and correlate
the newest profile post with the caption that was published."""

import re
import time
import urllib.parse
from datetime import datetime, timezone

from engine.telemetry import timed_telemetry_step
from modules.post_timestamp import parse_timestamp


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


def is_recent_timestamp_text(text: str) -> bool:
    """Parse a known timestamp and require a strict age below ten minutes."""
    parsed = parse_timestamp(text)
    return parsed is not None and parsed.recent


def permalink_not_requested() -> dict:
    """Return explicit metadata when no downstream action needs a permalink."""
    return {
        "post_url": None,
        "post_url_verified_at": None,
        "post_match_confidence": None,
        "permalink_status": "not_requested",
        "permalink_missing": False,
        "permalink_recovery_attempted": False,
    }


class Permalink:
    def __init__(self, task):
        self.task = task

    @property
    def telemetry(self):
        return getattr(self.task, "telemetry", None)

    @timed_telemetry_step("permalink_correlation")
    def correlate(
        self,
        caption: str | None = None,
        media_type: str = "post",
        max_scans: int = 4,
        time_budget_seconds: float | None = None,
    ) -> dict:
        """
        Navigate to profile, locate newly created post card via multi-signal correlation
        (caption tokens, recent timestamp, author), click the timestamp, and extract the URL.
        Returns a dict: { "post_url": str | None, "post_url_verified_at": str | None, "post_match_confidence": float }
        """
        task = self.task
        empty_res = {
            "post_url": None,
            "post_url_verified_at": None,
            "post_match_confidence": 0.0,
        }
        deadline = (
            time.monotonic() + max(0.0, time_budget_seconds)
            if time_budget_seconds is not None
            else None
        )

        task.log("STEP", "Navigating to profile to correlate published post and extract permalink...")
        try:
            task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
        except Exception as exc:
            task.log("WARN", f"Could not navigate to profile for permalink correlation: {exc}")
            return empty_res

        # Tokenize caption for correlation if provided
        search_tokens = set()
        if caption:
            normalized_cap = re.sub(r"[^a-z0-9]+", " ", caption.casefold()).strip()
            all_tokens = [w for w in normalized_cap.split() if len(w) >= 3]
            search_tokens = set(all_tokens[:8])

        for scan in range(1, max_scans + 1):
            if deadline is not None and time.monotonic() >= deadline:
                task.log("INFO", "Permalink correlation stopped at its time budget.")
                break
            screen = task.client.screenshot()
            ocr_items = task.vision.read_text(screen, min_confidence=0.18)

            caption_match_center = None
            timestamp_match_center = None
            timestamp_match_text = None
            timestamp_candidates = []
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
                if task._is_recent_timestamp_text(item["text"]):
                    timestamp_candidates.append(item)

            # Bind the timestamp to the matched post card. A recent timestamp
            # elsewhere on the feed must not be allowed to identify this post.
            if caption_match_center:
                cx, cy = caption_match_center
                nearby_timestamps = [
                    item for item in timestamp_candidates
                    if 0 <= cy - item["center"][1] <= 120
                    and abs(cx - item["center"][0]) <= 450
                ]
                if nearby_timestamps:
                    nearby_timestamps.sort(
                        key=lambda item: (
                            cy - item["center"][1],
                            -float(item.get("confidence", 0.0)),
                        )
                    )
                    timestamp_match_center = nearby_timestamps[0]["center"]
                    timestamp_match_text = nearby_timestamps[0]["text"]
            elif not search_tokens and timestamp_candidates:
                timestamp_match_center = timestamp_candidates[0]["center"]
                timestamp_match_text = timestamp_candidates[0]["text"]

            # If caption was matched, scan specifically in the header region above the caption for timestamp
            if caption_match_center and not timestamp_match_center:
                header_region = (
                    max(0, caption_match_center[0] - 220),
                    max(0, caption_match_center[1] - 80),
                    400,
                    70,
                )
                header_items = task.vision.read_text(screen, region=header_region, min_confidence=0.18)
                for h_item in header_items:
                    hx, hy = h_item["center"]
                    rx, ry, rw, rh = header_region
                    if (
                        rx <= hx <= rx + rw
                        and ry <= hy <= ry + rh
                        and task._is_recent_timestamp_text(h_item["text"])
                    ):
                        timestamp_match_center = h_item["center"]
                        timestamp_match_text = h_item["text"]
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

            task.log_decision(
                "Correlate published post",
                f"caption tokens={search_tokens or 'none'}, recent timestamp",
                f"scan={scan}, matched_tokens={matched_tokens_count}, ts_text={timestamp_match_text!r}, "
                f"ts_target={timestamp_match_center}, confidence={confidence:.2f}",
                "click timestamp link" if (confidence >= 0.50 and timestamp_match_center) else "scroll to next section",
                level="INFO",
            )

            # If we have a confident match with a clickable timestamp:
            if confidence >= 0.50 and timestamp_match_center:
                task.capture_evidence("before_permalink_click", screen, target=list(timestamp_match_center), confidence=round(confidence, 2))
                task.human.click(*timestamp_match_center)
                time.sleep(2.5)

                raw_url = task.client.get_current_url()
                is_valid, clean_url = task.validate_facebook_permalink(raw_url or "", post_type=media_type)

                after_click_screen = task.client.screenshot()
                task.capture_evidence(
                    "permalink_extraction",
                    after_click_screen,
                    raw_url=raw_url,
                    clean_url=clean_url,
                    is_valid=is_valid,
                    confidence=round(confidence, 2),
                )

                if is_valid and clean_url:
                    task.log("SUCCESS", f"Captured validated Facebook permalink: {clean_url} (confidence={confidence:.2f})")
                    return {
                        "post_url": clean_url,
                        "post_url_verified_at": datetime.now(timezone.utc).isoformat(),
                        "post_match_confidence": round(confidence, 2),
                    }
                else:
                    task.log("WARN", f"Clicked timestamp, but resulting URL was not a valid permalink: {raw_url}")

            # If not confident or timestamp not clickable, scroll down and scan next cards
            task.human.scroll("down", notches=3)
            if deadline is None:
                time.sleep(1.2)
            else:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                time.sleep(min(1.2, remaining))

        task.log("WARN", "Permalink correlation was ambiguous or not found; leaving post_url null.")
        task.capture_evidence("permalink_correlation_ambiguous")
        return empty_res

    def recover_missing(
        self,
        permalink_info: dict | None,
        *,
        caption: str | None,
        media_type: str,
        time_budget_seconds: float = 12.0,
    ) -> dict:
        """Run one short post-comment recovery pass only when the URL is missing."""
        task = self.task
        result = dict(permalink_info or {})
        result.setdefault("post_url", None)
        result.setdefault("post_url_verified_at", None)
        result.setdefault("post_match_confidence", 0.0)
        if result.get("post_url"):
            result.update({
                "permalink_status": "captured",
                "permalink_missing": False,
                "permalink_recovery_attempted": False,
            })
            return result

        task.log(
            "INFO",
            f"Initial permalink was unavailable; starting one bounded recovery pass "
            f"after comment handling (budget={time_budget_seconds:.0f}s).",
        )
        recovered = task.correlate_and_extract_permalink(
            caption=caption,
            media_type=media_type,
            max_scans=2,
            time_budget_seconds=time_budget_seconds,
        )
        if recovered.get("post_url"):
            result.update(recovered)
            result.update({
                "permalink_status": "recovered",
                "permalink_missing": False,
                "permalink_recovery_attempted": True,
            })
            return result

        result.update({
            "permalink_status": "missing",
            "permalink_missing": True,
            "permalink_recovery_attempted": True,
        })
        task.log("WARN", "Permalink recovery ended without a confident URL; publication remains successful.")
        return result

