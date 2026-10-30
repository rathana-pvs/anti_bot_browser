"""Natural navigation: click or refresh when sure, otherwise type the address."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.behavior_profile import BehaviorSession
from engine.screen_state import ScreenState, StateObservation
from skills.natural_navigation import (
    HOME_URL, PROFILE_URL, NaturalNavigation, load_identity, page_key, save_identity,
)
from skills.navigation import Navigation

PROFILE = "https://facebook.com/profile.php?id=100082211901107"


def host(*, natural=True, address="facebook.com", state=ScreenState.FEED_READY, sidebar=()):
    task = SimpleNamespace(profile_id="profile_test", log=Mock(), telemetry=None,
                           handle_leave_site_dialog=Mock(return_value=False), click_reversible=Mock())
    task.behavior_session = BehaviorSession("medium", 5, "nav", natural=natural)
    task.client = Mock()
    task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    task.client.get_current_url.return_value = None
    task.human = Mock()
    task.recognizer = Mock()
    task.recognizer.observe.return_value = StateObservation(state, 0.9, ["photo/video text"])
    addresses = [address] if isinstance(address, str) else list(address)

    def read_text(screen, region=None, min_confidence=0.35):
        if region[1] == 48:  # address bar
            value = addresses.pop(0) if len(addresses) > 1 else addresses[0]
            return [{"text": value, "bounds": (158, 52, 600, 72), "confidence": 0.99}] if value else []
        if region[0] == 0:  # left sidebar
            return [dict(item) for item in sidebar]
        return []

    task.vision = Mock()
    task.vision.read_text.side_effect = read_text
    return task


class NaturalNavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "profile_test").mkdir()
        self.profiles = patch("skills.natural_navigation.profiles_dir", return_value=root)
        self.profiles.start()
        self.idle = patch("skills.natural_navigation.browser_navigation_state", return_value="idle")
        self.idle.start()
        self.sleep = patch("skills.natural_navigation.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.idle.stop()
        self.profiles.stop()
        self.directory.cleanup()

    def test_address_bar_is_read_without_focusing_it(self):
        task = host(address="facebook.com/profile.php?id=42")
        self.assertEqual(NaturalNavigation(task).current_url(), "https://facebook.com/profile.php?id=42")
        task.client.get_current_url.assert_not_called()

    def test_unreadable_address_falls_back_to_the_copy_method(self):
        task = host(address="")
        task.client.get_current_url.return_value = "https://www.facebook.com/"
        self.assertEqual(NaturalNavigation(task).current_url(), "https://www.facebook.com/")

    def test_profile_pages_compare_by_id(self):
        self.assertEqual(page_key("facebook.com/profile.php?id=7&sk=reels"), "/profile.php?id=7")
        self.assertEqual(page_key("https://www.facebook.com/News429M/"), "/news429m")

    def test_already_on_own_profile_presses_f5(self):
        task = host(address="facebook.com/profile.php?id=100082211901107")
        save_identity(task, "News 429M", PROFILE)
        self.assertEqual(NaturalNavigation(task).go(PROFILE_URL), "refresh")
        task.human.key_press.assert_called_once_with("F5")
        task.human.navigate.assert_not_called()

    def test_clicks_own_name_in_the_sidebar_and_checks_the_address(self):
        task = host(address=["facebook.com", "facebook.com", "facebook.com/profile.php?id=100082211901107"],
                    sidebar=[{"text": "News 429M", "center": (120, 224), "bounds": (76, 214, 166, 234), "confidence": 0.98}])
        save_identity(task, "News 429M", PROFILE)
        navigation = NaturalNavigation(task)
        with patch.object(navigation, "_habit", return_value="sidebar"):
            self.assertEqual(navigation.go(PROFILE_URL), "sidebar")
        task.click_reversible.assert_called_once()
        self.assertEqual(task.click_reversible.call_args.args[0], (120, 224))

    def test_types_when_unsure(self):
        task = host(address="facebook.com")
        self.assertIsNone(NaturalNavigation(task).go(PROFILE_URL))  # name not learned yet
        save_identity(task, "News 429M", PROFILE)
        self.assertIsNone(NaturalNavigation(host(address="example.com")).go(PROFILE_URL))
        self.assertIsNone(NaturalNavigation(host(state=ScreenState.COMPOSER_OPEN)).go(PROFILE_URL))
        self.assertIsNone(NaturalNavigation(task).go("https://www.facebook.com/reel/1"))

    def test_logo_click_goes_home(self):
        task = host(address=["facebook.com/profile.php?id=1", "facebook.com/profile.php?id=1", "facebook.com"])
        self.assertEqual(NaturalNavigation(task).go(HOME_URL), "logo")
        task.human.click.assert_called_once_with(35, 115)

    def test_avatar_menu_without_the_name_is_closed(self):
        task = host(address="facebook.com")
        save_identity(task, "News 429M", PROFILE)
        task.composer_entry = SimpleNamespace(timing=SimpleNamespace(sample=lambda *a: 0))
        navigation = NaturalNavigation(task)
        self.assertFalse(navigation._click_avatar_name(task.client.screenshot(), load_identity(task)))
        task.human.key_press.assert_called_once_with("Escape")

    def test_go_to_types_when_natural_navigation_declines(self):
        task = host(address="facebook.com")
        with patch("skills.navigation.time.sleep"):
            Navigation(task).go_to(PROFILE_URL)
        task.human.navigate.assert_called_once_with(PROFILE_URL)

    def test_switch_off_always_types(self):
        task = host(natural=False, address="facebook.com/profile.php?id=100082211901107")
        save_identity(task, "News 429M", PROFILE)
        with patch("skills.navigation.time.sleep"):
            Navigation(task).go_to(PROFILE_URL)
        task.human.navigate.assert_called_once_with(PROFILE_URL)
        task.human.key_press.assert_not_called()

    def test_profile_name_is_learned_after_a_typed_visit(self):
        task = host(address="facebook.com/profile.php?id=100082211901107")
        task.vision.read_text.side_effect = lambda screen, region=None, min_confidence=0.35: (
            [{"text": "facebook.com/profile.php?id=100082211901107", "bounds": (158, 52, 600, 72), "confidence": 0.99}]
            if region[1] == 48 else
            [{"text": "News 429M", "bounds": (744, 196, 916, 228), "confidence": 0.99},
             {"text": "3K followers • 13 following", "bounds": (744, 236, 932, 252), "confidence": 0.99}])
        NaturalNavigation(task).remember_profile()
        identity = load_identity(task)
        self.assertEqual(identity["profile_name"], "News 429M")
        self.assertEqual(page_key(identity["profile_url"]), "/profile.php?id=100082211901107")


if __name__ == "__main__":
    unittest.main()
