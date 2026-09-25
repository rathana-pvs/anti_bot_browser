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
        self.log("STEP", "Starting Facebook warming session...")

        if not self.client.is_running():
            self.log("ERROR", f"Container {self.client.container_name} is not running.")
            return False

        # Step 1: Ensure window focus and navigate to Facebook
        self.log("STEP", "Navigating to facebook.com...")
        self.navigate_to("https://www.facebook.com", wait_seconds=random.uniform(3.5, 6.0))

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

        self.log("SUCCESS", "Facebook warming session completed successfully.")
        return True
