"""Session preflight: confirm the profile is logged in before any workflow."""

import re
import time
import urllib.parse

from engine.screen_state import ScreenState, StateObservation


def is_facebook_url(url: str | None) -> bool:
    """Accept Facebook itself, but never lookalike hosts containing its name."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        return parsed.scheme in {"http", "https"} and (
            hostname == "facebook.com" or hostname.endswith(".facebook.com")
        )
    except (TypeError, ValueError):
        return False


def has_strong_facebook_visual(observation: StateObservation) -> bool:
    """Require a Facebook-specific UI signal in addition to its URL host."""
    if observation.confidence < 0.75:
        return False

    signals = {signal.casefold() for signal in observation.signals}
    if observation.state == ScreenState.FEED_READY:
        strong_feed_signals = {
            "what's on your mind",
            "what’s on your mind",
            "whats on your mind",
            "what s on your mind",
            "photo/video",
            "photo / video",
            "create story",
            "your story",
            "professional dashboard",
            "photo/video text",
            "logged-in dashboard/feed text",
            "photo/video button visual",
        }
        return bool(signals & strong_feed_signals)

    # These states require Facebook-specific modal/action structure in the
    # recognizer, so their non-empty signals are independent visual proof.
    workflow_states = {
        ScreenState.COMPOSER_OPEN,
        ScreenState.MEDIA_UPLOADING,
        ScreenState.MEDIA_READY,
        ScreenState.POST_ENABLED,
        ScreenState.PUBLISHING,
        ScreenState.POST_CONFIRMED,
    }
    return observation.state in workflow_states and bool(signals - {"facebook logo"})


_PAGE_MARKERS = ("manage page", "professional dashboard", "creator dashboard", "business dashboard", "meta business suite")
# A Page's own feed also lists Memories, so only profile-page signs count.
_PERSONAL_MARKERS = re.compile(r"\b\d[\d.,]*k?\s+friends?\b|\badd to story\b", re.I)


def facebook_identity(items) -> str | None:
    """Whether Facebook shows a Page ("page") or the personal profile ("personal").

    A Page shows its management sidebar or dashboard; the personal profile shows
    a friend count or Add to story without them. Anything else stays unknown.
    """
    blob = " ".join(item.get("text", "") for item in items).casefold()
    if any(marker in blob for marker in _PAGE_MARKERS):
        return "page"
    return "personal" if _PERSONAL_MARKERS.search(blob) else None


class SessionCheck:
    def __init__(self, task):
        self.task = task

    def skip_unverified(self) -> bool:
        """Finish safely without treating an expired session as an automation failure."""
        task = self.task
        session_status = getattr(task, "session_check_status", "unverified")
        if session_status in {"auth_required", "safety_blocked"}:
            status = "skipped_auth_required"
            message = "Facebook is logged out, expired, restricted, or requires an account checkpoint."
        elif session_status == "personal_profile":
            status = "skipped_personal_profile"
            message = "Facebook is showing the personal profile instead of the Page, so nothing was posted. Switch back to the Page in the browser."
        else:
            status = "skipped_session_unverified"
            message = "Facebook did not reach a safely verified logged-in screen within 40 seconds."
        task.log("WARN", f"Skipping profile: {message} No login action will be attempted.")
        return task.set_outcome(
            status,
            error=message,
            auth_preflight=session_status,
        )

    def verify(self, target_url: str = "https://www.facebook.com/") -> bool:
        """
        Verify that the container Chrome session is logged into Facebook.
        Loading and transitional screens remain UNKNOWN and are polled rather
        than being mistaken for a logged-out session.
        """
        task = self.task
        task.log("INFO", "Verifying Facebook session login state...")
        task.navigate_to(
            target_url,
            wait_seconds=2.0,
            check_leave_dialog=False,
        )

        # Check window title first because it is cheap and works without OCR.
        try:
            title = task.client.active_window_name()
            if title is not None:
                title = title.lower()
                if any(k in title for k in ("log in", "login", "sign up", "welcome to facebook")):
                    task.session_check_status = "auth_required"
                    task.log("WARN", f"Session expired or logged out (Window title: {title})")
                    task.capture_evidence("session_preflight_auth_required")
                    return False
        except Exception:
            pass

        try:
            current_url = task.client.get_current_url()
            blocked_paths = ("/login", "/checkpoint", "/recover", "/two_step_verification")
            if current_url and any(path in current_url.casefold() for path in blocked_paths):
                task.session_check_status = "auth_required"
                task.log("WARN", f"Facebook authentication checkpoint detected at {current_url}")
                task.capture_evidence("session_preflight_auth_required")
                return False
        except Exception:
            pass

        # This is a safety preflight, not a login workflow. Profiles are assumed
        # to be authenticated; a short bounded check only prevents blind actions
        # after an expired session, checkpoint, or unexpected page.
        deadline = time.time() + 40.0
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
        navigation_retried = False
        while time.time() < deadline:
            screen = task.client.screenshot()
            # Navigation can still show the previous page after the address bar
            # changes. Refresh theme from each actual screenshot so browser
            # chrome or a prior light page cannot lock Facebook into light-mode
            # preprocessing.
            task.detect_visual_theme(screen)
            observation = task.recognizer.observe_session_gate(screen)
            if "leave_site_dialog" in observation.signals:
                if task.handle_leave_site_dialog(screen):
                    time.sleep(1.0)
                    continue
            if observation.state == ScreenState.UNKNOWN:
                observation = task.recognizer.observe(screen)
            if observation.state == ScreenState.LOGIN_REQUIRED:
                break
            if observation.state == ScreenState.ERROR_DIALOG:
                break
            if observation.state in valid_logged_in_states:
                try:
                    current_url = task.client.get_current_url()
                except Exception:
                    current_url = None
                has_facebook_host = task._is_facebook_url(current_url)
                has_facebook_visual = task._has_strong_facebook_visual(observation)
                if has_facebook_host and has_facebook_visual:
                    break
                task.log(
                    "INFO",
                    "Ignoring an unconfirmed/loading browser page "
                    f"(url={current_url or 'unknown'}, "
                    f"facebook_visual={has_facebook_visual}, signals={observation.signals}).",
                )
                if not navigation_retried and not has_facebook_host:
                    task.log(
                        "INFO",
                        "The browser remained outside Facebook after the first navigation; retrying the target URL once.",
                    )
                    task.navigate_to(
                        target_url,
                        wait_seconds=2.0,
                        check_leave_dialog=False,
                    )
                    navigation_retried = True
                observation = StateObservation(
                    ScreenState.UNKNOWN,
                    0.0,
                    ["facebook_host_or_visual_not_confirmed"],
                )
            if observation.state == ScreenState.UNKNOWN:
                if task.handle_leave_site_dialog(screen):
                    time.sleep(1.0)
                    continue
            task.log("INFO", "Facebook is still loading or the screen is transitional; checking again...")
            time.sleep(2.0)

        if observation is None or screen is None:
            task.session_check_status = "unverified"
            task.log("ERROR", "Facebook session verification produced no visual observation.")
            return False

        task.capture_evidence(
            "login_check",
            screen,
            state=observation.state.value,
            confidence=observation.confidence,
            signals=observation.signals,
        )
        if observation.state == ScreenState.LOGIN_REQUIRED:
            task.session_check_status = "auth_required"
            task.log("WARN", "Facebook login screen was detected; this profile will be skipped.")
            return False
        if observation.state == ScreenState.ERROR_DIALOG:
            task.session_check_status = "safety_blocked"
            task.log("WARN", "Facebook displayed a restriction, checkpoint, CAPTCHA, or error; this profile will be skipped.")
            return False
        if observation.state not in valid_logged_in_states:
            task.session_check_status = "unverified"
            task.log("WARN", "Facebook session remained visually unverified after the 40-second safety timeout.")
            return False
        task.session_check_status = "authenticated"
        try:
            task.facebook_identity = facebook_identity(task.vision.read_text(screen, min_confidence=0.6))
        except Exception as exc:
            task.log("DEBUG", f"Could not tell whether Facebook shows the Page or the personal profile: {exc}")
        if getattr(task, "facebook_identity", None) == "personal":
            # Anything done now would happen as the personal profile.
            task.session_check_status = "personal_profile"
            task.log("WARN", "Facebook is showing the personal profile, not the Page; this profile will be skipped.")
            return False
        return True

