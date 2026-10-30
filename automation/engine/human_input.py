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

    def _natural(self) -> bool:
        """Natural session switch on: use the account's persona for input detail."""
        return getattr(self.behavior_session, "natural", False) is True

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

    def move_to(self, target_x: int, target_y: int, duration_sec: float | None = None,
                *, target_width: float | None = None) -> None:
        """
        Move the mouse cursor smoothly from current position to (target_x, target_y)
        following a randomized cubic Bezier curve with realistic velocity easing.
        """
        if self._natural() and duration_sec is None:
            self._move_natural(target_x, target_y, target_width)
            return
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

    def click(self, x: int | None = None, y: int | None = None, *, target_width: float | None = None) -> None:
        """
        Move to (x, y) if coordinates provided, then execute human-timed click:
        press down, hold for 50-120ms, release.
        """
        if self._natural():
            self._click_natural(x, y, target_width)
            return
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

    def type_text(self, text: str, wpm: int | None = None, *, waiter=None, prose: bool = True,
                  allow_typos: bool = False) -> dict:
        """Type a complete supported caption; line breaks never emit Return.

        ``prose=False`` drops sentence and phrase pauses, for URLs and similar
        strings where punctuation is not a reading break. ``allow_typos`` is
        only for fields the caller reads back and verifies after typing.
        """
        if any(not (' ' <= char <= '~') for char in text):
            raise ValueError("Use full paste for Unicode or multiline content")
        if self._natural():
            return self._type_natural(text, wpm, waiter, prose, allow_typos)
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

    # Natural session input --------------------------------------------------
    # Used only with the natural session switch on; every path above stays as
    # it was when the switch is off.

    def _glide(self, start, end, duration: float, rng) -> None:
        """A curved path with smooth tremor and uneven step timing."""
        (start_x, start_y), (end_x, end_y) = start, end
        dx, dy = end_x - start_x, end_y - start_y
        dist = math.hypot(dx, dy)
        if dist < 1:
            return
        perp_x, perp_y = -dy / dist, dx / dist
        dev1 = rng.uniform(-0.2, 0.2) * dist
        dev2 = rng.uniform(-0.1, 0.1) * dist
        cp1 = (start_x + dx * 0.3 + perp_x * dev1, start_y + dy * 0.3 + perp_y * dev1)
        cp2 = (start_x + dx * 0.7 + perp_x * dev2, start_y + dy * 0.7 + perp_y * dev2)
        steps = max(8, min(45, int(duration * 60)))
        # A hand trembles slowly, not with a fresh random jump at every step.
        cycles = rng.uniform(1.5, 3.5)
        phase_x, phase_y = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
        amplitude = min(1.2, 0.3 + dist / 900)
        base_sleep = duration / steps
        path = []
        for i in range(1, steps + 1):
            t = _ease_in_out(i / steps)
            if i < steps:
                x = _cubic_bezier(start_x, cp1[0], cp2[0], end_x, t) + amplitude * math.sin(2 * math.pi * cycles * t + phase_x)
                y = _cubic_bezier(start_y, cp1[1], cp2[1], end_y, t) + amplitude * math.sin(2 * math.pi * cycles * t + phase_y)
            else:
                x, y = end_x, end_y
            path.append((int(round(x)), int(round(y)), base_sleep * rng.uniform(0.7, 1.3)))
        if hasattr(self.client, "pointer_path"):
            self.client.pointer_path(path)
            return
        for x, y, delay in path:
            self.client.pointer_move(x, y)
            time.sleep(delay)

    def _fitts_seconds(self, dist: float, width: float | None) -> float:
        """Movement time grows with distance and shrinks with target size."""
        width = max(8.0, float(width or 40.0))
        return 0.08 + 0.11 * math.log2(dist / width + 1)

    def _move_natural(self, target_x: int, target_y: int, target_width: float | None) -> None:
        session = self.behavior_session
        persona = session.persona
        rng = self._rng("mouse")
        start_x, start_y = self.client.get_mouse_position()
        dx, dy = target_x - start_x, target_y - start_y
        dist = math.hypot(dx, dy)
        if dist < 2:
            return
        duration = self._fitts_seconds(dist, target_width) / persona.pointer_speed * session.pace
        duration = min(1.3, max(0.12, duration * rng.uniform(0.9, 1.15)))
        if dist > 250 and rng.random() < persona.overshoot_rate:
            # Long moves sometimes pass the target and come back.
            ux, uy = dx / dist, dy / dist
            past = rng.uniform(6.0, min(35.0, dist * 0.08))
            side = rng.uniform(-6.0, 6.0)
            over = (target_x + ux * past - uy * side, target_y + uy * past + ux * side)
            self._glide((start_x, start_y), over, duration * 0.85, rng)
            time.sleep(self._duration("mouse", rng.uniform(0.05, 0.14)))
            correction = max(0.08, self._fitts_seconds(math.hypot(past, side), target_width) / persona.pointer_speed)
            self._glide(over, (target_x, target_y), correction, rng)
        else:
            self._glide((start_x, start_y), (target_x, target_y), duration, rng)
        time.sleep(self._duration("mouse", rng.uniform(0.04, 0.09)))

    def _click_natural(self, x, y, target_width) -> None:
        rng = self._rng("click")
        persona = self.behavior_session.persona
        if x is not None and y is not None:
            # Never the exact same pixel: a small spread around the confirmed
            # point, kept within two pixels so small buttons are still hit.
            x = int(x) + int(round(max(-2.0, min(2.0, rng.gauss(0.0, 1.0)))))
            y = int(y) + int(round(max(-2.0, min(2.0, rng.gauss(0.0, 1.0)))))
            self.move_to(x, y, target_width=target_width)
        hold_scale = 1.1 / persona.pointer_speed
        time.sleep(self._duration("click", rng.uniform(0.03, 0.08)))
        self.client.button("down")
        time.sleep(self._duration("click", rng.uniform(0.05, 0.11) * hold_scale))
        self.client.button("up")
        time.sleep(self._duration("click", rng.uniform(0.05, 0.12)))

    def idle(self, seconds: float) -> None:
        """Wait, now and then letting the resting pointer drift a few pixels."""
        if not self._natural():
            time.sleep(seconds)
            return
        rng = self._rng("mouse")
        end = time.monotonic() + max(0.0, seconds)
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            pause = min(remaining, rng.uniform(1.5, 5.0))
            time.sleep(pause)
            if end - time.monotonic() > 0.4 and rng.random() < 0.35:
                x, y = self.client.get_mouse_position()
                target = (x + rng.randint(-12, 12), y + rng.randint(-8, 8))
                self._glide((x, y), target, rng.uniform(0.15, 0.35), rng)

    def _key_delay_scale(self, previous: str, char: str) -> float:
        pair = (previous + char).lower()
        if pair in _COMMON_PAIRS:
            return 0.75
        if char.isupper():
            return 1.35
        if char.isdigit() or (not char.isalnum() and char != " "):
            return 1.25
        if previous == char:
            return 0.85
        return 1.0

    def _type_natural(self, text, wpm, waiter, prose, allow_typos) -> dict:
        session = self.behavior_session
        persona = session.persona
        rng = self._rng("typing")
        wpm = wpm or session.typing_speed()
        if wpm <= 0:
            raise ValueError("WPM must be positive")
        waiter = waiter or InteractionWait()
        start = waiter.clock()
        dispatch_time = pause_time = 0.0
        phrase_wpm = wpm
        phrase_words = 0
        typos = 0
        previous = ""
        last_was_typo = False
        waiter.checkpoint()
        # A short look at the field before the first key.
        waiter.pause(rng.uniform(0.3, 1.2) * persona.reading_speed * session.pace)

        def key_delay(char, overhead):
            base = 60 / (phrase_wpm * 5) * self._key_delay_scale(previous, char)
            return max(0, base * rng.lognormvariate(0.0, 0.18) - overhead)

        for char in text:
            if (allow_typos and char.isalpha() and char.lower() in _NEIGHBORS and not last_was_typo
                    and rng.random() < persona.typo_rate):
                wrong = rng.choice(_NEIGHBORS[char.lower()])
                wrong = wrong.upper() if char.isupper() else wrong
                waiter.check()
                self.client.type_character(wrong)
                waiter.pause(key_delay(wrong, 0.0) + rng.uniform(0.15, 0.45))
                waiter.check()
                self.client.key("BackSpace")
                waiter.pause(rng.uniform(0.08, 0.2))
                typos += 1
                last_was_typo = True
            else:
                last_was_typo = False
            before = waiter.clock()
            waiter.check()
            self.client.type_character(char)
            overhead = waiter.clock() - before
            dispatch_time += overhead
            delay = key_delay(char, overhead)
            if prose and char in '.!?':
                delay += rng.triangular(.4, 1.2, .7)
                phrase_words = 0
            elif prose and char == ' ':
                phrase_words += 1
                if phrase_words >= 5:
                    delay += rng.triangular(.2, .7, .35)
                    phrase_words = 0
                    waiter.checkpoint()
                    low, high = session.preset.typing_wpm
                    phrase_wpm = min(high, max(low, wpm * rng.uniform(.9, 1.1)))
            previous = char
            before_pause = waiter.clock()
            waiter.pause(delay)
            pause_time += waiter.clock() - before_pause
        elapsed = waiter.clock() - start
        return {"elapsed_seconds": elapsed, "dispatch_seconds": dispatch_time,
                "pause_seconds": pause_time, "typos_corrected": typos,
                "effective_wpm": (len(text) / 5) * 60 / elapsed if elapsed else 0}


_COMMON_PAIRS = frozenset({
    "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd", "ti", "es", "or", "te", "of",
    "ed", "is", "it", "al", "ar", "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le",
})

# Physical neighbors on a US QWERTY keyboard, for realistic slips.
_KEY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_NEIGHBORS = {}
for _row_index, _row in enumerate(_KEY_ROWS):
    for _column, _key in enumerate(_row):
        _near = [_row[c] for c in (_column - 1, _column + 1) if 0 <= c < len(_row)]
        for _other in (_row_index - 1, _row_index + 1):
            if 0 <= _other < len(_KEY_ROWS):
                _near += [_KEY_ROWS[_other][c] for c in (_column - 1, _column, _column + 1) if 0 <= c < len(_KEY_ROWS[_other])]
        _NEIGHBORS[_key] = tuple(_near)
