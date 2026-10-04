"""Facebook Account Warming Task.

Simulates organic human browsing on Facebook:
  - Feed scrolling and variable reading pauses
  - Natural mouse wandering and jitter
  - Configurable passive browsing with a bounded session
"""

import random
import time
from .base_task import BaseTask
from warming_options import normalize_warming_options


class FacebookWarmingTask(BaseTask):
    def __init__(self, profile_id: str, scroll_count: int = 4, warming_options: dict | None = None):
        super().__init__(profile_id)
        self.scroll_count = max(1, min(30, scroll_count))
        self.warming_options = normalize_warming_options(warming_options)

    def run(self) -> bool:
        options = normalize_warming_options(getattr(self, "warming_options", None))
        planned_scrolls = (random.randint(options["min_scrolls"], options["max_scrolls"])
                           if options["random_scrolls"] else self.scroll_count)
        self.set_stage("warming", scroll_count=planned_scrolls)
        self.log("STEP", "Starting Facebook warming session...")

        if not self.client.is_running():
            self.log("ERROR", f"Container {self.client.container_name} is not running.")
            return self.set_outcome(
                "failed_before_publish",
                reason="profile_container_not_running",
            )

        # Step 1: Pick the browsing surface first so authentication and warming
        # share one page load.
        requested_surface = (random.choice(tuple(self.WARMING_SURFACES))
                             if options["surface"] == "random" else options["surface"])
        self.log("STEP", "Verifying the Facebook session before passive browsing...")
        if not self.verify_logged_in(
            target_url=self.WARMING_SURFACES[requested_surface]
        ):
            return self.skip_unverified_session()

        requested_surface, warm_surface, fallback_used, surface_screen = (
            self.select_and_open_warming_surface(
                requested=requested_surface,
                already_open=requested_surface,
            )
        )
        if warm_surface is None:
            if getattr(self, "session_check_status", "") == "auth_required":
                return self.skip_unverified_session()
            return self.set_outcome(
                "failed_before_publish",
                reason="warming_surface_unavailable",
                warming_surface_requested=requested_surface,
            )

        # Budget starts after authentication and surface verification.
        started = time.monotonic()
        deadline = started + options["max_seconds"]
        scroll_actions = 0
        completed_cycles = 0
        reading_range = {"quick": (1.0, 3.0), "balanced": (2.5, 5.5), "relaxed": (5.0, 10.0)}[options["pace"]]

        def pause(seconds):
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(seconds, remaining))

        def move_cursor():
            w, h = self.client.get_screen_dimensions()
            self.human.move_to(random.randint(int(w * 0.25), int(w * 0.75)),
                               random.randint(int(h * 0.2), int(h * 0.8)))

        self.log("STEP", f"Browsing {warm_surface}: {planned_scrolls} cycles, {options['pace']} pace")
        if options["cursor_movement"]:
            for _ in range(random.randint(2, 4)):
                if time.monotonic() >= deadline:
                    break
                move_cursor()
                pause(random.uniform(0.8, 2.2))

        for i in range(planned_scrolls):
            if time.monotonic() >= deadline:
                break
            notches = random.randint(2, 5)
            self.log("INFO", f"Scroll {i + 1}/{planned_scrolls}: down {notches} notches")
            self.human.scroll("down", notches=notches)
            scroll_actions += 1
            completed_cycles += 1
            pause(random.uniform(*reading_range))
            if time.monotonic() >= deadline:
                break
            if options["reread"] and random.random() < 0.25:
                self.log("INFO", "Scrolling back slightly to reread")
                self.human.scroll("up", notches=random.randint(1, 2))
                scroll_actions += 1
                pause(random.uniform(*reading_range))
            if time.monotonic() >= deadline:
                break
            if options["cursor_movement"] and random.random() < 0.35:
                move_cursor()
            if options["long_breaks"] and i < planned_scrolls - 1 and random.random() < 0.2:
                self.log("INFO", "Taking a longer reading break")
                pause(random.uniform(8.0, 15.0))

        if options["return_to_top"] and time.monotonic() < deadline:
            self.human.scroll("up", notches=random.randint(3, 6))
            scroll_actions += 1
            pause(random.uniform(1.0, 2.5))
        duration = round(time.monotonic() - started, 2)
        self.log("INFO", f"Browsing finished: {completed_cycles} cycles, {scroll_actions} scrolls in {duration}s")

        self.capture_evidence(
            "warming_ready",
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
        )
        self.log("SUCCESS", "Facebook warming session completed successfully.")
        return self.set_outcome(
            "completed",
            scroll_count=completed_cycles,
            planned_scroll_count=planned_scrolls,
            scroll_actions=scroll_actions,
            duration_seconds=duration,
            warming_options=options,
            time_limit_reached=time.monotonic() >= deadline,
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
        )
