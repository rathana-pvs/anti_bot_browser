"""Shared post-click prompt, upload, and closure verification for every Reel template.

This module never clicks Post or navigates. It returns profile_fallback only
once the composer has closed with no error dialog; the Reel task accepts that
as publication.
"""

from __future__ import annotations

import re
import time

from engine.screen_state import ScreenState


# OCR sometimes drops the % sign or reads it as a trailing 9 ("849").
_UPLOAD_PERCENT = re.compile(r"uploading\s*(\d{1,3})\s*%?", re.I)


class ReelPostClickVerifier:
    """Own the Not now -> fresh upload wait -> confirmation sequence."""

    # A slow proxy can still be uploading when the normal wait ends. The wait
    # continues only while Facebook's "Uploading N%" keeps rising.
    MAX_UPLOAD_SECONDS = 240.0
    UPLOAD_STALL_SECONDS = 30.0
    # Facebook's "Hosting an event?" upsell holds the Reel until its
    # "Publish Original Post" button is pressed.
    EVENT_PROMPT_ATTEMPTS = 3
    EVENT_PROMPT_SETTLE_SECONDS = 3.0

    def __init__(self, task):
        self.task = task
        self.upload_extension = None
        self.event_prompt_clicks = 0
        self.event_prompt_status = "not_seen"

    def _handle_event_prompt(self, screen) -> str:
        """Press "Publish Original Post" on the event upsell, never "Continue".

        The modal belongs to the Post click already sent, like the audio
        policy step, so this completes that one attempt rather than posting
        again. Returns absent, clicked, closed or failed.
        """
        finder = getattr(self.task, "find_publish_original_post", None)
        if finder is None:
            return "absent"
        match = finder(screen)
        if match is None:
            if self.event_prompt_clicks:
                self.event_prompt_status = "closed"
                self.task.log("INFO", "The \"Hosting an event?\" prompt closed after Publish Original Post.")
                return "closed"
            return "absent"
        if self.event_prompt_clicks >= self.EVENT_PROMPT_ATTEMPTS:
            self.event_prompt_status = "failed"
            self.task.log("ERROR", f"The \"Hosting an event?\" prompt stayed open after "
                          f"{self.EVENT_PROMPT_ATTEMPTS} Publish Original Post clicks.")
            self.task.capture_evidence("reel_event_prompt_failed", screen, attempts=self.event_prompt_clicks)
            return "failed"
        self.event_prompt_clicks += 1
        self.event_prompt_status = "clicked"
        self.task.log("INFO", f"Facebook asked \"Hosting an event?\" after Post. Attempt "
                      f"{self.event_prompt_clicks}/{self.EVENT_PROMPT_ATTEMPTS}: clicking Publish Original Post "
                      f"at {match['center']} (never Continue).")
        self.task.capture_evidence("reel_event_prompt_publish_original", screen,
                                   target=list(match["center"]), attempt=self.event_prompt_clicks)
        self.task.click_reversible(match["center"], label="reel_event_prompt_publish_original",
                                   bounds=match.get("bounds"), max_offset_px=4)
        time.sleep(self.EVENT_PROMPT_SETTLE_SECONDS)
        return "clicked"

    def upload_percent(self, screen) -> int | None:
        """The percentage Facebook shows on the Reel preview while uploading."""
        if screen is None:
            return None
        height, width = screen.shape[:2]
        # The label sits on the preview's centre line; a tighter crop
        # separates it from a news headline drawn behind it.
        regions = (
            ((int(width * .5), int(height * .5), int(width * .3), int(height * .15)), 0.5),
            ((int(width * .58), int(height * .56), int(width * .15), int(height * .04)), 0.3),
        )
        for region, confidence in regions:
            values = []
            for item in self.task.vision.read_text(screen, region=region, min_confidence=confidence):
                match = _UPLOAD_PERCENT.search(item.get("text", ""))
                if match:
                    value = int(match.group(1))
                    values.append(value if value <= 100 else value // 10)
            if values:
                return max(values)
        return None

    def _upload_still_moving(self, screen, now: float, hard_deadline: float) -> bool:
        """After the normal wait, keep waiting only while the upload progresses."""
        percent = self.upload_percent(screen)
        extension = self.upload_extension
        if extension is None:
            if percent is None:
                return False
            extension = self.upload_extension = {
                "started_at": now, "first_percent": percent, "last_percent": percent,
                "progressed_at": now, "stop_reason": None,
            }
            self.task.log("INFO", f"The Reel is still uploading ({percent}%); waiting while the upload keeps moving.")
            return True
        if percent is not None and percent > extension["last_percent"]:
            extension["last_percent"] = percent
            extension["progressed_at"] = now
            self.task.log("INFO", f"Reel upload progress {percent}%.")
        if now >= hard_deadline:
            extension["stop_reason"] = "max_wait"
        elif now - extension["progressed_at"] >= self.UPLOAD_STALL_SECONDS:
            extension["stop_reason"] = "stalled"
        else:
            return True
        extension["extended_seconds"] = round(now - extension["started_at"], 1)
        return False

    def _wait(self, seconds: float) -> None:
        """Natural sessions let the resting pointer drift during the upload wait."""
        if getattr(getattr(self.task, "behavior_session", None), "natural", False) is True:
            self.task.human.idle(seconds)
        else:
            time.sleep(seconds)

    def composer_has_closed(self, screen, observation) -> bool:
        """Require feed/confirmation evidence and exclude the full Reel composer."""
        if screen is None or observation.state not in {
            ScreenState.FEED_READY, ScreenState.POST_CONFIRMED,
        }:
            return False
        # The publication gate only sees the bottom-left action area. An overlay
        # can hide those controls while the composer is still open elsewhere.
        items = self.task.vision.read_text(screen, min_confidence=0.15)
        blob = " ".join(self.task._normalized_ocr_text(item.get("text", "")) for item in items)
        composer_markers = (
            "create reel", "reels create", "describe your reel", "uploaded media", "replace media",
            "choose thumbnail", "remixing and use of original audio", "uploading",
            "add whatsapp button", "make it easier to contact you",
            "speak with people directly", "post settings", "post audience",
        )
        lines = [
            " ".join(self.task._normalized_ocr_text(item.get("text", "")) for item in line)
            for line in self.text_lines(items)
        ]
        # After Post, Facebook may land on the profile's Reels tab, whose
        # header has its own "Create reel" link next to "Your reels" and
        # "Saved reels". That link is not the composer; the other composer
        # markers still block if a composer is open over the tab.
        reels_tab = (any("your reels" in line for line in lines)
                     and any("saved reels" in line for line in lines))
        if reels_tab:
            composer_markers = tuple(m for m in composer_markers if m not in {"create reel", "reels create"})
        if any(marker in line for line in lines for marker in composer_markers):
            return False
        if observation.state == ScreenState.POST_CONFIRMED:
            return True
        feed_markers = (
            "manage page", "professional dashboard", "meta business suite",
            "what s on your mind", "whats on your mind", "photo video",
            "your story", "create story",
        )
        # A Facebook logo alone cannot prove that the composer closed.
        return (
            reels_tab
            or any(marker in blob for marker in feed_markers)
            or all(word in blob.split() for word in ("like", "comment", "share"))
            or all(word in blob.split() for word in ("posts", "friends", "photos"))
        )

    @staticmethod
    def text_lines(items):
        """Group OCR fragments that sit side by side on one visual line.

        Composer phrases must not be matched across the whole screen: the
        Page sidebar's "Boost Instagram post" above "Settings" then read as
        "post settings" and kept a closed composer counted as open. Items
        without bounds (test doubles) each form their own line.
        """
        lines = [[item] for item in items if not item.get("bounds")]
        placed = sorted((item for item in items if item.get("bounds")), key=lambda item: item["bounds"][0])
        rows = []
        for item in placed:
            left, top, right, bottom = item["bounds"]
            middle = (top + bottom) / 2
            max_gap = max(24, 1.5 * (bottom - top))
            for row in rows:
                last_left, last_top, last_right, last_bottom = row[-1]["bounds"]
                if last_top <= middle <= last_bottom and -5 <= left - last_right <= max_gap:
                    row.append(item)
                    break
            else:
                rows.append([item])
        return lines + rows

    def allow_profile_navigation(self) -> bool:
        """Check closure once, immediately before the first profile navigation.

        Opening the profile replaces the composer page, so later refreshes
        cannot leave an open composer behind. Rechecking them only misread a
        slowly loading profile (Facebook's logo screen) as an open composer.
        """
        if (getattr(self.task, "_reel_profile_opened", False) is True
                or getattr(self.task, "_reel_composer_closed", False) is True):
            return True
        screen = self.task.client.screenshot()
        observation = self.task.recognizer.observe(screen)
        if self.composer_has_closed(screen, observation):
            return True
        self.task._uncertain(
            "reel_composer_closure_unconfirmed",
            "The Reel composer has not been confirmed closed; profile refresh was suppressed.",
            screen,
        )
        return False

    def verify(
        self,
        before_publish,
        timeout: float = 90.0,
        popup_timeout: float = 30.0,
    ):
        upload_deadline = time.time() + timeout
        hard_deadline = upload_deadline - timeout + max(timeout, self.MAX_UPLOAD_SECONDS)
        popup_deadline = None
        last = None
        prompt_dismissed = False
        unconfirmed_feed_logged = False
        audio_policy_saved = False
        local_misses = 0
        while True:
            now = time.time()
            screen = self.task.client.screenshot()
            # Handle the blocking prompt before expensive publication OCR, from
            # the first poll. Start a fresh upload wait after it closes.
            if not prompt_dismissed:
                prompt_status = self.task.handle_post_publish_prompt(screen)
                if prompt_status == "failed":
                    return "failed", last
                if prompt_status == "dismissed":
                    prompt_dismissed = True
                    now = time.time()
                    upload_deadline = now + timeout
                    hard_deadline = now + max(timeout, self.MAX_UPLOAD_SECONDS)
                    popup_deadline = None
                    self.task.log("INFO", "Dismissed post-publish prompt ('Not now').")
                    self.task.log("INFO", f"Starting a fresh {timeout:g}-second composer/upload wait after Not now closed.")
                    screen = self.task.client.screenshot()

            # Check the event upsell until it has closed; the upload only
            # starts once Publish Original Post is pressed, so each press gets
            # a fresh upload wait.
            if self.event_prompt_status != "closed":
                event_status = self._handle_event_prompt(screen)
                if event_status == "failed":
                    return "event_prompt_blocked", (
                        self.task.recognizer.observe(screen), screen,
                        self.task.vision.similarity(before_publish, screen),
                    )
                if event_status in {"clicked", "closed"}:
                    now = time.time()
                    upload_deadline = now + timeout
                    hard_deadline = now + max(timeout, self.MAX_UPLOAD_SECONDS)
                    popup_deadline = None
                if event_status == "clicked":
                    continue

            observation = self.task.recognizer.observe_publication_gate(
                screen,
                region=self.task.LEFT_PUBLICATION_REGION,
            )
            if observation.state == ScreenState.UNKNOWN:
                broad_observation = self.task.recognizer.observe(screen)
                if broad_observation.state != ScreenState.UNKNOWN:
                    observation = broad_observation
            similarity = self.task.vision.similarity(before_publish, screen)
            last = (observation, screen, similarity)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "failed", last
            composer_closed = self.composer_has_closed(screen, observation)
            if observation.state == ScreenState.POST_CONFIRMED and composer_closed:
                self.task.log("INFO", "Facebook showed Reel publication confirmation.")
                return "published", last

            # Facebook can insert this settings step after the final Post click.
            # It is part of the same publication attempt, so confirm the current
            # selection once and continue verification without clicking Post again.
            should_check_audio_policy = local_misses in {1, 4}
            if not audio_policy_saved and should_check_audio_policy:
                audio_policy_status = self.task._handle_remix_audio_dialog(
                    screen,
                    region="composer_modal",
                )
                if audio_policy_status == "saved":
                    audio_policy_saved = True
                    time.sleep(2.0)
                    continue
                if audio_policy_status == "waiting":
                    time.sleep(2.0)
                    continue

            local_misses += 1
            if not composer_closed and now >= upload_deadline:
                if self._upload_still_moving(screen, now, hard_deadline):
                    self._wait(2.0)
                    continue
                extension = self.upload_extension
                if extension:
                    self.task.log("WARN", f"Reel upload stopped at {extension['last_percent']}% ({extension['stop_reason']}) "
                                  f"after {extension['extended_seconds']:g} extra seconds; stopping without refreshing it.")
                else:
                    self.task.log("WARN", f"Reel composer closure was not confirmed within {timeout:g} seconds; stopping without refreshing it.")
                return "timed_out", last
            if composer_closed and popup_deadline is not None and now >= popup_deadline:
                self.task.log("WARN", f"Reel success popup was not detected within {popup_timeout:g} seconds after the loading composer closed; checking the profile feed.")
                return "profile_fallback", last
            if not composer_closed:
                popup_deadline = None
                self._wait(2.0)
                continue
            if observation.state == ScreenState.FEED_READY:
                if not unconfirmed_feed_logged:
                    self.task.log(
                        "INFO",
                        f"The loading composer closed and the profile feed is visible; waiting up to {popup_timeout:g} seconds for the Reel success popup.",
                    )
                    unconfirmed_feed_logged = True
                if popup_deadline is None:
                    popup_deadline = now + popup_timeout
            self._wait(2.0)
