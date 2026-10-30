"""Human Input Simulation Engine.

Provides mathematically realistic human-like mouse trajectories (cubic Bezier curves),
natural micro-jitter, variable typing cadence (WPM modeling), and organic scrolling
for bounded interaction pacing. No detection guarantee is implied.
"""

import math
import random
import time
from .driver import Driver
from .behavior_timing import InteractionWait


def _cubic_bezier(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Evaluate cubic Bezier formula at parameter t in [0, 1]."""
    return (
        (1 - t) ** 3 * p0
        + 3 * (1 - t) ** 2 * t * p1
        + 3 * (1 - t) * t ** 2 * p2
        + t ** 3 * p3
    )


def _ease_in_out(t: float) -> float:
    """Non-linear easing curve for acceleration and deceleration."""
    if t < 0.5:
        return 2 * t * t
    return -1 + (4 - 2 * t) * t


class HumanInput:
    def __init__(self, client: Driver, behavior_session=None):
        self.client = client
        self.behavior_session = behavior_session

    def _rng(self, channel: str):
        if self.behavior_session is not None:
            return self.behavior_session.stream(channel)
        return random

    def _duration(self, channel: str, value: float) -> float:
        if self.behavior_session is not None:
            return self.behavior_session.scaled_duration(channel, value)
        return value

    @staticmethod
    def safe_click_point(
        bounds: tuple[int, int, int, int],
        max_offset_px: int = 6,
        inset_ratio: float = 0.22,
        rng=None,
    ) -> tuple[int, int]:
        """Choose a small, center-biased point that remains safely inside bounds.

        This is intended for large, reversible controls whose complete bounds were
        visually confirmed. Small controls naturally collapse back toward their
        center because the inset margin takes priority over variation.
        """
        rng = rng if rng is not None else random
        x1, y1, x2, y2 = (int(value) for value in bounds)
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        width = right - left
        height = bottom - top
        if width <= 2 or height <= 2 or max_offset_px <= 0:
            return center_x, center_y

        inset_x = min(max(2, int(round(width * inset_ratio))), max(0, width // 2 - 1))
        inset_y = min(max(2, int(round(height * inset_ratio))), max(0, height // 2 - 1))
        safe_left = left + inset_x
        safe_right = right - inset_x
        safe_top = top + inset_y
        safe_bottom = bottom - inset_y

        offset_x = min(max_offset_px, center_x - safe_left, safe_right - center_x)
        offset_y = min(max_offset_px, center_y - safe_top, safe_bottom - center_y)
        target_x = center_x + rng.randint(-max(0, offset_x), max(0, offset_x))
        target_y = center_y + rng.randint(-max(0, offset_y), max(0, offset_y))
        return target_x, target_y

    def move_to(self, target_x: int, target_y: int, duration_sec: float | None = None) -> None:
        """
        Move the mouse cursor smoothly from current position to (target_x, target_y)
        following a randomized cubic Bezier curve with realistic velocity easing.
        """
        start_x, start_y = self.client.get_mouse_position()
        dist = math.hypot(target_x - start_x, target_y - start_y)

        # Skip movement if already within 2 pixels
        if dist < 2:
            return

        # Determine step count and duration based on distance
        rng = self._rng("mouse")
        if duration_sec is None:
            # 0.2s for short distances up to 0.7s for cross-screen
            duration_sec = self._duration(
                "mouse",
                min(0.7, max(0.2, dist / 2000.0 + rng.uniform(0.1, 0.25))),
            )

        steps = max(12, min(35, int(dist / 40.0) + rng.randint(8, 14)))

        # Calculate Bezier control points with random perpendicular deviation
        dx = target_x - start_x
        dy = target_y - start_y
        norm = math.hypot(dx, dy)
        perp_x = -dy / norm
        perp_y = dx / norm

        # Random control point deviations
        dev1 = rng.uniform(-0.25, 0.25) * dist
        dev2 = rng.uniform(-0.15, 0.15) * dist

        cp1_x = start_x + dx * 0.3 + perp_x * dev1
        cp1_y = start_y + dy * 0.3 + perp_y * dev1
        cp2_x = start_x + dx * 0.7 + perp_x * dev2
        cp2_y = start_y + dy * 0.7 + perp_y * dev2

        points = []
        for i in range(1, steps + 1):
            raw_t = i / steps
            eased_t = _ease_in_out(raw_t)

            bx = _cubic_bezier(start_x, cp1_x, cp2_x, target_x, eased_t)
            by = _cubic_bezier(start_y, cp1_y, cp2_y, target_y, eased_t)

            # Micro-jitter along trajectory
            if i < steps:
                bx += rng.uniform(-1.0, 1.0)
                by += rng.uniform(-1.0, 1.0)
            else:
                bx = target_x
                by = target_y

            points.append((int(bx), int(by)))

        # Execute smooth movement
        step_sleep = duration_sec / steps
        for px, py in points:
            self.client.pointer_move(px, py)
            time.sleep(step_sleep)

        # Micro-settle at destination
        time.sleep(self._duration("mouse", rng.uniform(0.04, 0.09)))

    def click(self, x: int | None = None, y: int | None = None) -> None:
        """
        Move to (x, y) if coordinates provided, then execute human-timed click:
        press down, hold for 50-120ms, release.
        """
        if x is not None and y is not None:
            self.move_to(x, y)

        rng = self._rng("click")
        time.sleep(self._duration("click", rng.uniform(0.03, 0.08)))
        self.client.button("down")
        time.sleep(self._duration("click", rng.uniform(0.05, 0.11)))
        self.client.button("up")
        time.sleep(self._duration("click", rng.uniform(0.05, 0.12)))

    def double_click(self, x: int | None = None, y: int | None = None) -> None:
        """Execute double click with realistic interval."""
        if x is not None and y is not None:
            self.move_to(x, y)

        self.click()
        rng = self._rng("click")
        time.sleep(self._duration("click", rng.uniform(0.08, 0.15)))
        self.click()

    def type_text(self, text: str, wpm: int | None = None, *, waiter=None, prose: bool = True) -> dict:
        """Type a complete supported caption; line breaks never emit Return.

        ``prose=False`` drops sentence and phrase pauses, for URLs and similar
        strings where punctuation is not a reading break.
        """
        if any(not (' ' <= char <= '~') for char in text):
            raise ValueError("Use full paste for Unicode or multiline content")
        rng = self._rng("typing")
        wpm = wpm or (self.behavior_session.typing_speed() if self.behavior_session else 55)
        if wpm <= 0:
            raise ValueError("WPM must be positive")
        waiter = waiter or InteractionWait()
        start = waiter.clock()
        dispatch_time = pause_time = 0.0
        phrase_wpm = wpm
        phrase_words = 0
        waiter.checkpoint()
        for char in text:
            before = waiter.clock()
            waiter.check()
            self.client.type_character(char)
            overhead = waiter.clock() - before
            dispatch_time += overhead
            delay = max(0, 60 / (phrase_wpm * 5) * rng.uniform(.85, 1.15) - overhead)
            if prose and char in '.!?':
                delay += rng.triangular(.4, 1.2, .7)
                phrase_words = 0
            elif prose and char == ' ':
                phrase_words += 1
                if phrase_words >= 5:
                    delay += rng.triangular(.2, .7, .35)
                    phrase_words = 0
                    waiter.checkpoint()
                    low, high = self.behavior_session.preset.typing_wpm if self.behavior_session else (1, 200)
                    phrase_wpm = min(high, max(low, wpm * rng.uniform(.9, 1.1)))
            before_pause = waiter.clock()
            waiter.pause(delay)
            pause_time += waiter.clock() - before_pause
        elapsed = waiter.clock() - start
        return {"elapsed_seconds": elapsed, "dispatch_seconds": dispatch_time,
                "pause_seconds": pause_time,
                "effective_wpm": (len(text) / 5) * 60 / elapsed if elapsed else 0}

    def scroll(self, direction: str = "down", notches: int = 3) -> None:
        """
        Scroll using mouse wheel with variable notch speed and pauses.
        direction: 'down' (Button 5) or 'up' (Button 4).
        """
        rng = self._rng("scroll")
        for _ in range(notches):
            self.client.wheel(direction)
            time.sleep(self._duration("scroll", rng.uniform(0.08, 0.22)))
        time.sleep(self._duration("scroll", rng.uniform(0.3, 0.8)))

    def key_press(self, key_name: str, *, clear_modifiers: bool = False, check: bool = True) -> None:
        """Press a keyboard key or combination (e.g. 'Return', 'ctrl+a', 'BackSpace')."""
        self.client.key(key_name, clear_modifiers=clear_modifiers, check=check)
        rng = self._rng("click")
        time.sleep(self._duration("click", rng.uniform(0.08, 0.18)))

    def type_native(self, text: str) -> None:
        """Enter text into a native dialog outside the page, such as the GTK
        file chooser. The page cannot observe it, so no cadence is applied."""
        self.client.type_string(text)

    def navigate(self, url: str, *, waiter=None) -> None:
        """Open a URL through the address bar with human key timing."""
        if not self.client.focus_address_bar():
            self.key_press("ctrl+l", clear_modifiers=True)
            time.sleep(0.2)
        self.key_press("ctrl+a", clear_modifiers=True)
        if all(' ' <= char <= '~' for char in url):
            self.type_text(url, waiter=waiter, prose=False)
        else:
            self.client.write_clipboard(url)
            self.key_press("ctrl+v", clear_modifiers=True)
        time.sleep(self._duration("pause", self._rng("pause").uniform(0.15, 0.4)))
        # Chrome may select a history-completed suffix (e.g. /live/producer/)
        # after typing the Facebook root URL. Delete only that selected suffix;
        # at an unselected end-of-line this is a no-op.
        self.key_press("Delete", clear_modifiers=True)
        self.key_press("Return", clear_modifiers=True)
