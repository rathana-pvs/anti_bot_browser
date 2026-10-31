"""Moving between Facebook surfaces: address-bar navigation, refresh, and the
feed/profile surfaces used for passive browsing."""

import time

from engine.screen_state import ScreenState
from skills.natural_navigation import PROFILE_URL, NaturalNavigation


class Navigation:
    def __init__(self, task):
        self.task = task

    def go_to(
        self,
        url: str,
        wait_seconds: float = 2.0,
        check_leave_dialog: bool = True,
    ) -> None:
        """
        Navigate to URL via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        task = self.task
        started = time.perf_counter()
        outcome = "completed"
        natural = NaturalNavigation(task)
        method = None
        try:
            if natural.enabled():
                try:
                    method = natural.go(url)
                except Exception as exc:
                    task.log("WARN", f"Natural navigation fell back to typing the address: {exc}")
            if method is None and getattr(task, "human", None) is not None:
                task.human.navigate(url)
                method = "typed"
            time.sleep(1.0)
            if check_leave_dialog:
                task.handle_leave_site_dialog()
            if wait_seconds > 1.0:
                time.sleep(wait_seconds - 1.0)
            if natural.enabled():
                natural._count(f"navigation_{method}")
                if method == "typed" and url == PROFILE_URL:
                    try:
                        natural.remember_profile()
                    except Exception as exc:
                        task.log("DEBUG", f"Could not remember the profile name: {exc}")
        except Exception:
            outcome = "exception"
            raise
        finally:
            telemetry = getattr(task, "telemetry", None)
            if telemetry is not None:
                telemetry.record_navigation(
                    url, (time.perf_counter() - started) * 1000.0, outcome,
                    method=method if natural.enabled() else None,
                )

    def open_surface(
        self,
        surface: str,
        timeout: float = 15.0,
        *,
        navigate: bool = True,
    ):
        """Navigate to and verify one passive Facebook browsing surface."""
        task = self.task
        url = task.WARMING_SURFACES.get(surface)
        if url is None:
            raise ValueError(f"Unknown warming surface: {surface}")

        task.log("INFO", f"Opening warming surface: {surface}")
        if navigate:
            task.navigate_to(url, wait_seconds=2.0, check_leave_dialog=False)
        else:
            task.log("INFO", f"Reusing already verified warming surface: {surface}")
        deadline = time.time() + timeout
        last_screen = None
        valid_states = {
            ScreenState.FEED_READY,
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
        }
        while time.time() < deadline:
            last_screen = task.client.screenshot()
            observation = task.recognizer.observe_session_gate(last_screen)
            if observation.state == ScreenState.UNKNOWN:
                observation = task.recognizer.observe(last_screen)
            if observation.state == ScreenState.LOGIN_REQUIRED:
                task.session_check_status = "auth_required"
                return False, last_screen
            if observation.state == ScreenState.ERROR_DIALOG:
                return False, last_screen
            if observation.state in valid_states:
                try:
                    current_url = task.current_url()
                except Exception:
                    current_url = None
                if task._is_facebook_url(current_url) and task._has_strong_facebook_visual(observation):
                    return True, last_screen
            time.sleep(0.75)
        return False, last_screen

    def select_and_open_surface(
        self,
        requested: str | None = None,
        already_open: str | None = None,
    ):
        """Choose either feed or profile independently, with one safe fallback."""
        task = self.task
        requested = requested or task.rng("warming").choice(tuple(task.WARMING_SURFACES))
        if requested not in task.WARMING_SURFACES:
            raise ValueError(f"Unknown warming surface: {requested}")
        fallback = next(surface for surface in task.WARMING_SURFACES if surface != requested)
        for attempt, surface in enumerate((requested, fallback), start=1):
            if attempt == 1 and surface == already_open:
                ready, screen = task.open_warming_surface(surface, navigate=False)
            else:
                ready, screen = task.open_warming_surface(surface)
            if ready:
                used_fallback = attempt == 2
                task.log(
                    "INFO",
                    f"Warming surface ready: {surface}"
                    + (f" (fallback from {requested})" if used_fallback else ""),
                )
                return requested, surface, used_fallback, screen
            if getattr(task, 'session_check_status', '') in {'auth_required', 'safety_blocked'}:
                return requested, None, attempt == 2, screen
            if attempt == 1:
                task.log("WARN", f"Warming surface '{requested}' was not ready; trying '{fallback}' once.")
        return requested, None, True, screen

    def refresh(self, wait_seconds: float = 2.0) -> None:
        """
        Refresh active browser page via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        task = self.task
        natural = NaturalNavigation(task)
        if natural.enabled():
            natural.refresh()
        elif hasattr(task, "client") and task.client is not None:
            task.client.refresh_page()
        time.sleep(1.0)
        task.handle_leave_site_dialog()
        if wait_seconds > 1.0:
            time.sleep(wait_seconds - 1.0)

