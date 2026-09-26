"""Facebook Account Warming Task.

Simulates organic human browsing on Facebook:
  - Feed scrolling and variable reading pauses
  - Natural mouse wandering and jitter
  - Safe engagement without triggering automation detection
"""

import random
import time
from .base_task import BaseTask


class FacebookWarmingTask(BaseTask):
    def __init__(self, profile_id: str, scroll_count: int = 4):
        super().__init__(profile_id)
        self.scroll_count = scroll_count

    def run(self) -> bool:
        self.set_stage("warming", scroll_count=self.scroll_count)
        self.log("STEP", "Starting Facebook warming session...")

        if not self.client.is_running():
            self.log("ERROR", f"Container {self.client.container_name} is not running.")
            return self.set_outcome(
                "failed_before_publish",
                reason="profile_container_not_running",
            )

        # Step 1: Verify the authenticated session and stop on any checkpoint.
        self.log("STEP", "Verifying the Facebook session before passive browsing...")
        if not self.verify_logged_in():
            return self.skip_unverified_session()

        requested_surface, warm_surface, fallback_used, surface_screen = (
            self.select_and_open_warming_surface()
        )
        if warm_surface is None:
            if getattr(self, "session_check_status", "") == "auth_required":
                return self.skip_unverified_session()
            return self.set_outcome(
                "failed_before_publish",
                reason="warming_surface_unavailable",
                warming_surface_requested=requested_surface,
            )

        # Step 2: Human mouse movement across page
        w, h = self.client.get_screen_dimensions()
        for _ in range(random.randint(2, 4)):
            rand_x = random.randint(int(w * 0.25), int(w * 0.75))
            rand_y = random.randint(int(h * 0.2), int(h * 0.8))
            self.log("INFO", f"Human browsing cursor movement to ({rand_x}, {rand_y})")
            self.human.move_to(rand_x, rand_y)
            time.sleep(random.uniform(0.8, 2.2))

        # Step 3: Natural feed scrolling
        self.log("STEP", f"Browsing feed with {self.scroll_count} simulated scroll actions...")
        for i in range(self.scroll_count):
            notches = random.randint(2, 5)
            self.log("INFO", f"Scroll step {i + 1}/{self.scroll_count}: down {notches} notches")
            self.human.scroll("down", notches=notches)

            # Reading pause
            read_pause = random.uniform(2.5, 5.5)
            self.log("INFO", f"Reading pause for {read_pause:.1f}s")
            time.sleep(read_pause)

        # Step 4: Scroll back towards top
        self.log("STEP", "Scrolling back towards top of feed...")
        self.human.scroll("up", notches=random.randint(3, 6))
        time.sleep(random.uniform(1.0, 2.5))

        self.capture_evidence(
            "warming_ready",
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
        )
        self.log("SUCCESS", "Facebook warming session completed successfully.")
        return self.set_outcome(
            "completed",
            scroll_count=self.scroll_count,
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
        )
