"""An auto warming visit: a short chain of read-only activities.

Activities are feed, notifications, the Page's Reels tab, the Page itself and
idle pauses, chosen from the account's persona and a fresh draw each visit.
Nothing is liked, commented, shared, followed or typed; clicks only open a
place, and each is checked by what appears afterwards.

Between activities, and between scrolls inside one, is a safe point. There the
visit stops when the backend asks it to give way to a post, when the machine
is under pressure, or when its time is up.
"""

import time

from engine.passive_browsing import PassiveBrowsing
from engine.resource_signal import read_yield
from skills.natural_navigation import HOME_URL, PROFILE_URL, page_key

ACTIVITY_WEIGHTS = {"feed": 0.4, "own_page": 0.25, "reels": 0.2, "notifications": 0.15, "idle": 0.05}


class ActiveVisit:
    def __init__(self, task, max_seconds: float):
        self.task = task
        self.rng = task.rng("warming")
        signal = task.resource_signal()
        self.started = time.monotonic()
        self.started_wall = time.time()
        self.deadline = self.started + max_seconds * signal["elastic_scale"]
        self.stop_reason = None
        self.done = []
        # When the current activity should hand over to the next one.
        self.activity_until = self.deadline

    # Safe points -------------------------------------------------------------

    def should_stop(self) -> bool:
        if self.stop_reason:
            return True
        reason = read_yield(self.task.profile_id, self.started_wall)
        if reason:
            self.stop_reason = f"yield:{reason}"
        elif self.task.resource_signal()["hold"]:
            self.stop_reason = "machine_pressure"
        elif time.monotonic() >= self.deadline:
            self.stop_reason = "time_up"
        return self.stop_reason is not None

    def pause(self, seconds: float) -> None:
        """Wait in short pieces so a stop request is noticed within a few seconds."""
        end = min(time.monotonic() + seconds, self.deadline)
        while time.monotonic() < end and not self.should_stop():
            time.sleep(min(2.0, end - time.monotonic()))

    # Planning ----------------------------------------------------------------

    def plan(self, surface: str) -> list[str]:
        """Two to four activities, starting where the browser already is."""
        first = "own_page" if surface == "profile" else "feed"
        others = [name for name in ACTIVITY_WEIGHTS if name not in (first, "idle")]
        self.rng.shuffle(others)
        chain = [first] + others[:self.rng.randint(1, 3)]
        if self.rng.random() < 0.3:
            chain.insert(self.rng.randint(1, len(chain)), "idle")
        return chain

    def run(self, surface: str) -> dict:
        chain = self.plan(surface)
        for index, activity in enumerate(chain):
            if self.should_stop():
                break
            # Each activity gets its weighted share of the time that is left.
            remaining = max(0.0, self.deadline - time.monotonic())
            weights = [ACTIVITY_WEIGHTS[name] for name in chain[index:]]
            self.activity_until = time.monotonic() + remaining * weights[0] / sum(weights)
            self.task.log("INFO", f"Visit activity {index + 1}/{len(chain)}: {activity.replace('_', ' ')}.")
            try:
                getattr(self, f"_{activity}")()
                self.done.append(activity)
            except Exception as exc:
                self.task.log("WARN", f"Visit activity {activity} stopped early: {exc}")
        self._close_panels()
        return {"duration_seconds": round(time.monotonic() - self.started, 1),
                "stop_reason": self.stop_reason or "finished"}

    # Activities --------------------------------------------------------------

    def _browse(self) -> None:
        """Scroll with reading pauses until this activity's time is up, then return up."""
        task = self.task
        screen = task.client.screenshot()
        height, width = screen.shape[:2]
        browsing = PassiveBrowsing(task.behavior_session, width, height)
        until = max(self.activity_until, time.monotonic() + 15.0)
        previous, first = screen, True
        while time.monotonic() < until and not self.should_stop():
            step = browsing.next_step()
            if step is None:
                break
            target = browsing.pointer_target(task.client.get_mouse_position(), first=first)
            first = False
            if target is not None:
                task.human.move_to(*target)
            task.human.scroll(step.direction, notches=step.notches)
            self.pause(step.pause_seconds)
            current = task.client.screenshot()
            left, top, right, bottom = browsing.bounds
            browsing.record(step, task.vision.similarity(previous[top:bottom, left:right], current[top:bottom, left:right]) < .985)
            previous = current
        while browsing.net_down > 0:
            notches = min(browsing.net_down, self.rng.randint(2, 4))
            task.human.scroll("up", notches=notches)
            browsing.net_down -= notches
            time.sleep(self.rng.uniform(.3, .7))

    def _feed(self) -> None:
        if page_key(self.task.current_url()) not in ("/", "/home.php"):
            self.task.navigate_to(HOME_URL, wait_seconds=3.0)
        self._browse()

    def _own_page(self) -> None:
        self.task.navigate_to(PROFILE_URL, wait_seconds=3.0)
        self._browse()

    def _notifications(self) -> None:
        task = self.task
        screen = task.client.screenshot()
        height, width = screen.shape[:2]
        # The bell is the third icon from the right in Facebook's top bar.
        task.human.click(width - 99, int(height * 0.106))
        time.sleep(self.rng.uniform(1.2, 2.0))
        if not self._notifications_open():
            task.human.key_press("Escape")
            return
        task.human.move_to(int(width * 0.86), int(height * 0.5))
        while time.monotonic() < self.activity_until - 4 and not self.should_stop():
            self.pause(self.rng.uniform(3.0, 8.0))
            task.human.scroll("down", notches=self.rng.randint(1, 3))
        self.pause(self.rng.uniform(2.0, 4.0))
        task.human.key_press("Escape")
        time.sleep(0.8)

    def _notifications_open(self, screen=None) -> bool:
        screen = self.task.client.screenshot() if screen is None else screen
        height, width = screen.shape[:2]
        items = self.task.vision.read_text(screen, region=(int(width * .6), int(height * .1), int(width * .4), int(height * .14)),
                                           min_confidence=.6)
        return any(item.get("text", "").strip().casefold() == "notifications" for item in items)

    def _reels(self) -> None:
        task = self.task
        if page_key(task.current_url()) in ("/", "/home.php"):
            task.navigate_to(PROFILE_URL, wait_seconds=3.0)
        screen = task.client.screenshot()
        height, width = screen.shape[:2]
        items = task.vision.read_text(screen, region=(int(width * .25), int(height * .3), int(width * .5), int(height * .12)),
                                      min_confidence=.7)
        tab = next((item for item in items if item.get("text", "").strip().casefold() == "reels"), None)
        if tab is None:
            return
        task.click_reversible(tab["center"], label="page_reels_tab", bounds=tab.get("bounds"), max_offset_px=4)
        time.sleep(self.rng.uniform(1.5, 2.5))
        self._browse()

    def _idle(self) -> None:
        self.task.human.idle(min(self.rng.uniform(5.0, 20.0), max(0.0, self.activity_until - time.monotonic())))

    def _close_panels(self) -> None:
        try:
            if self._notifications_open():
                self.task.human.key_press("Escape")
        except Exception:
            pass
