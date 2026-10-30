"""Natural-session input: Fitts timing, overshoot, click spread, checked typos."""

import dataclasses
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.behavior_profile import BehaviorSession
from engine.behavior_timing import InteractionWait
from engine.human_input import HumanInput, _NEIGHBORS


class FakeDriver:
    """Records input and keeps a text field that honours BackSpace."""

    def __init__(self, start=(100, 100)):
        self.position = start
        self.points = []
        self.buttons = []
        self.keys = []
        self.field = ""

    def get_mouse_position(self):
        return self.position

    def pointer_move(self, x, y):
        self.position = (x, y)
        self.points.append((x, y))

    def button(self, action, button=1):
        self.buttons.append(action)

    def type_character(self, char):
        self.field += char

    def key(self, name, *, clear_modifiers=False, check=True):
        self.keys.append(name)
        if name == "BackSpace":
            self.field = self.field[:-1]


class Clock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


def human(natural=True, **persona):
    session = BehaviorSession("medium", 4242, "input", natural=natural)
    if persona:
        session.persona = dataclasses.replace(session.persona, **persona)
    driver = FakeDriver()
    return HumanInput(driver, session), driver


class NaturalMouseTests(unittest.TestCase):
    def timed_move(self, target, width, **persona):
        hand, driver = human(**persona)
        clock = Clock()
        with patch("engine.human_input.time.sleep", clock.sleep):
            hand.move_to(*target, target_width=width)
        return clock.now, driver

    def test_far_small_targets_take_longer_than_near_large_ones(self):
        far_small, _ = self.timed_move((1700, 900), 12, overshoot_rate=0.0)
        near_large, _ = self.timed_move((160, 120), 200, overshoot_rate=0.0)
        self.assertGreater(far_small, near_large * 1.8)

    def test_long_moves_can_overshoot_and_still_end_on_target(self):
        _, driver = self.timed_move((1500, 100), 40, overshoot_rate=1.0)
        self.assertEqual(driver.points[-1], (1500, 100))
        self.assertGreater(max(x for x, _ in driver.points), 1503)

    def test_paths_are_smooth_rather_than_jumpy(self):
        _, driver = self.timed_move((1100, 700), 40, overshoot_rate=0.0)
        jumps = [math.dist(a, b) for a, b in zip(driver.points, driver.points[1:])]
        self.assertTrue(all(jump < 120 for jump in jumps))
        self.assertGreater(len(driver.points), 8)

    def test_clicks_spread_within_two_pixels(self):
        hits = set()
        for run in range(40):
            hand, driver = human()
            hand.behavior_session = BehaviorSession("medium", 4242, f"click-{run}", natural=True)
            with patch("engine.human_input.time.sleep"):
                hand.click(500, 300)
            x, y = driver.points[-1]
            self.assertLessEqual(abs(x - 500), 2)
            self.assertLessEqual(abs(y - 300), 2)
            self.assertEqual(driver.buttons, ["down", "up"])
            hits.add((x, y))
        self.assertGreater(len(hits), 3)

    def test_idle_drifts_only_a_few_pixels(self):
        hand, driver = human()
        driver.position = (800, 400)
        clock = Clock()
        with patch("engine.human_input.time.sleep", clock.sleep), \
             patch("engine.human_input.time.monotonic", clock.clock):
            hand.idle(30.0)
        self.assertTrue(all(abs(x - 800) <= 30 and abs(y - 400) <= 30 for x, y in driver.points))
        hand_off, driver_off = human(natural=False)
        with patch("engine.human_input.time.sleep"):
            hand_off.idle(5.0)
        self.assertEqual(driver_off.points, [])


class NaturalTypingTests(unittest.TestCase):
    def type(self, text, **kwargs):
        persona = kwargs.pop("persona", {})
        hand, driver = human(**persona)
        clock = Clock()
        waiter = InteractionWait(clock=clock.clock, sleep=clock.sleep)
        stats = hand.type_text(text, waiter=waiter, **kwargs)
        return stats, driver, clock

    def test_typos_are_corrected_so_the_field_is_exact(self):
        text = "Breaking news tonight from the capital"
        stats, driver, _ = self.type(text, allow_typos=True, persona={"typo_rate": 1.0})
        self.assertEqual(driver.field, text)
        self.assertGreater(stats["typos_corrected"], 5)
        self.assertEqual(driver.keys.count("BackSpace"), stats["typos_corrected"])

    def test_no_typos_unless_the_caller_verifies_the_field(self):
        stats, driver, _ = self.type("www.facebook.com", prose=False, persona={"typo_rate": 1.0})
        self.assertEqual(driver.field, "www.facebook.com")
        self.assertEqual(driver.keys, [])
        self.assertEqual(stats["typos_corrected"], 0)

    def test_slips_hit_neighbouring_keys(self):
        self.assertIn("s", _NEIGHBORS["a"])
        self.assertIn("w", _NEIGHBORS["e"])
        self.assertNotIn("p", _NEIGHBORS["a"])

    def test_thinking_pause_comes_before_the_first_key(self):
        _, _, natural_clock = self.type("ok")
        hand, driver = human(natural=False)
        clock = Clock()
        hand.type_text("ok", waiter=InteractionWait(clock=clock.clock, sleep=clock.sleep))
        self.assertGreater(natural_clock.now, clock.now + 0.2)


if __name__ == "__main__":
    unittest.main()
