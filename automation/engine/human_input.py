"""Human Input Simulation Engine.

Provides mathematically realistic human-like mouse trajectories (cubic Bezier curves),
natural micro-jitter, variable typing cadence (WPM modeling), and organic scrolling
to ensure 0% behavioral bot detection.
"""

import math
import random
import time
from .container_client import ContainerClient


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
    def __init__(self, client: ContainerClient):
        self.client = client

    @staticmethod
    def safe_click_point(
        bounds: tuple[int, int, int, int],
        max_offset_px: int = 6,
        inset_ratio: float = 0.22,
    ) -> tuple[int, int]:
        """Choose a small, center-biased point that remains safely inside bounds.

        This is intended for large, reversible controls whose complete bounds were
        visually confirmed. Small controls naturally collapse back toward their
        center because the inset margin takes priority over variation.
        """
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
        target_x = center_x + random.randint(-max(0, offset_x), max(0, offset_x))
        target_y = center_y + random.randint(-max(0, offset_y), max(0, offset_y))
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
        if duration_sec is None:
            # 0.2s for short distances up to 0.7s for cross-screen
            duration_sec = min(0.7, max(0.2, dist / 2000.0 + random.uniform(0.1, 0.25)))

        steps = max(12, min(35, int(dist / 40.0) + random.randint(8, 14)))

        # Calculate Bezier control points with random perpendicular deviation
        dx = target_x - start_x
        dy = target_y - start_y
        norm = math.hypot(dx, dy)
        perp_x = -dy / norm
        perp_y = dx / norm

        # Random control point deviations
        dev1 = random.uniform(-0.25, 0.25) * dist
        dev2 = random.uniform(-0.15, 0.15) * dist

        cp1_x = start_x + dx * 0.3 + perp_x * dev1
        cp1_y = start_y + dy * 0.3 + perp_y * dev1
        cp2_x = start_x + dx * 0.7 + perp_y * dev2
        cp2_y = start_y + dy * 0.7 + perp_y * dev2

        commands = []
        for i in range(1, steps + 1):
            raw_t = i / steps
            eased_t = _ease_in_out(raw_t)

            bx = _cubic_bezier(start_x, cp1_x, cp2_x, target_x, eased_t)
            by = _cubic_bezier(start_y, cp1_y, cp2_y, target_y, eased_t)

            # Micro-jitter along trajectory
            if i < steps:
                bx += random.uniform(-1.0, 1.0)
                by += random.uniform(-1.0, 1.0)
            else:
                bx = target_x
                by = target_y

            commands.append(f"mousemove {int(bx)} {int(by)}")

        # Execute smooth movement
        step_sleep = duration_sec / steps
        for cmd in commands:
            self.client.xdo(cmd)
            time.sleep(step_sleep)

        # Micro-settle at destination
        time.sleep(random.uniform(0.04, 0.09))

    def click(self, x: int | None = None, y: int | None = None) -> None:
        """
        Move to (x, y) if coordinates provided, then execute human-timed click:
        press down, hold for 50-120ms, release.
        """
        if x is not None and y is not None:
            self.move_to(x, y)

        time.sleep(random.uniform(0.03, 0.08))
        self.client.xdo("mousedown 1")
        time.sleep(random.uniform(0.05, 0.11))
        self.client.xdo("mouseup 1")
        time.sleep(random.uniform(0.05, 0.12))

    def double_click(self, x: int | None = None, y: int | None = None) -> None:
        """Execute double click with realistic interval."""
        if x is not None and y is not None:
            self.move_to(x, y)

        self.click()
        time.sleep(random.uniform(0.08, 0.15))
        self.click()

    def type_text(self, text: str, wpm: int = 55) -> None:
        """
        Type text character-by-character at a natural WPM cadence,
        introducing natural pauses after punctuation and simulated thinking stops.
        """
        base_char_delay = 60.0 / (wpm * 5)  # Average 5 chars per word

        for idx, char in enumerate(text):
            # Special character handling for xdotool
            if char == "\n":
                self.client.xdo("key Return")
                time.sleep(random.uniform(0.2, 0.45))
                continue
            elif char == " ":
                self.client.xdo("key space")
                delay = base_char_delay * random.uniform(0.9, 1.4)
            elif char in ("'", '"', "$", "&", "<", ">", "|", "\\"):
                # Use key or escaped type
                self.client.xdo(f"type -- {char}")
                delay = base_char_delay * random.uniform(0.8, 1.3)
            else:
                self.client.xdo(f"type -- {char}")
                delay = base_char_delay * random.uniform(0.7, 1.25)

            # Extra thinking pause after punctuation
            if char in (".", "!", "?"):
                delay += random.uniform(0.25, 0.6)
            elif char in (",", ";", ":"):
                delay += random.uniform(0.15, 0.35)

            # Random 4% chance of a hesitation pause (thinking/looking at keyboard)
            if random.random() < 0.04:
                delay += random.uniform(0.2, 0.5)

            time.sleep(delay)

    def scroll(self, direction: str = "down", notches: int = 3) -> None:
        """
        Scroll using mouse wheel with variable notch speed and pauses.
        direction: 'down' (Button 5) or 'up' (Button 4).
        """
        btn = "5" if direction.lower() == "down" else "4"
        for _ in range(notches):
            self.client.xdo(f"click {btn}")
            time.sleep(random.uniform(0.08, 0.22))
        time.sleep(random.uniform(0.3, 0.8))

    def key_press(self, key_name: str) -> None:
        """Press a keyboard key or combination (e.g. 'Return', 'ctrl+a', 'BackSpace')."""
        self.client.xdo(f"key {key_name}")
        time.sleep(random.uniform(0.08, 0.18))
