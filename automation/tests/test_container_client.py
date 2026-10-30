"""Focused tests for Chrome window discovery."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.behavior_timing import InteractionWait
from engine.container_client import ContainerClient
from engine.human_input import HumanInput


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class ChromeWindowDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = ContainerClient("test-profile")
        self.client.exec_cmd = Mock()

    def test_returns_largest_visible_chrome_window(self):
        self.client.exec_cmd.side_effect = [
            completed(stdout="101\n202\n303\n"),
            completed(stdout="Geometry: 800x600\n"),
            completed(stdout="Geometry: 1280x720\n"),
            completed(stdout="Geometry: 10x10\n"),
        ]

        self.assertEqual(self.client.get_chrome_window(), 202)

    def test_returns_zero_when_all_geometry_lookups_fail(self):
        self.client.exec_cmd.side_effect = [
            completed(stdout="101\n202\n"),
            completed(returncode=1),
            completed(returncode=1),
        ]

        self.assertEqual(self.client.get_chrome_window(), 0)

    def test_falls_back_to_nonvisible_window_search(self):
        self.client.exec_cmd.side_effect = [
            completed(returncode=1),
            completed(stdout="303\n"),
            completed(stdout="Geometry: 1280x720\n"),
        ]

        self.assertEqual(self.client.get_chrome_window(), 303)


class ChromeNavigationTests(unittest.TestCase):
    def test_navigation_clicks_address_bar_and_types_at_human_pace(self):
        client = ContainerClient("test-profile")
        client.ensure_focus = Mock(return_value=55)
        client.exec_cmd = Mock(
            side_effect=lambda *args, **kwargs: completed(
                stdout="X=0\nY=0\nWIDTH=1920\nHEIGHT=1080\n"
            )
        )
        now = [0.0]
        waiter = InteractionWait(
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

        with patch("engine.container_client.time.sleep", return_value=None), \
             patch("engine.human_input.time.sleep", return_value=None):
            HumanInput(client).navigate("https://fb.com/", waiter=waiter)

        commands = [call.args[0] for call in client.exec_cmd.call_args_list]
        self.assertEqual(
            commands[:4],
            [
                ["xdotool", "getwindowgeometry", "--shell", "55"],
                ["xdotool", "mousemove", "--window", "55", "960", "61"],
                ["xdotool", "click", "1"],
                ["xdotool", "key", "--clearmodifiers", "ctrl+a"],
            ],
        )
        typed = commands[4:-2]
        self.assertEqual(
            typed,
            [["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", char]
             for char in "https://fb.com/"],
        )
        self.assertEqual(
            commands[-2:],
            [
                ["xdotool", "key", "--clearmodifiers", "Delete"],
                ["xdotool", "key", "--clearmodifiers", "Return"],
            ],
        )
        # Characters are spaced by typing pauses, not sent as one burst.
        self.assertGreater(now[0], 0.1 * len("https://fb.com/"))

    def test_url_read_clicks_address_bar_before_copying(self):
        client = ContainerClient("test-profile")
        client.ensure_focus = Mock(return_value=55)
        client.exec_cmd = Mock(
            side_effect=[
                completed(stdout="X=0\nY=0\nWIDTH=1920\nHEIGHT=1080\n"),
                completed(),
                completed(),
                completed(stdout="https://www.facebook.com/me"),
            ]
        )

        self.assertEqual(client.get_current_url(), "https://www.facebook.com/me")

        calls = [call.args[0] for call in client.exec_cmd.call_args_list]
        self.assertEqual(
            calls[1],
            ["xdotool", "mousemove", "--window", "55", "960", "61"],
        )
        script = calls[-1][-1]
        self.assertIn("xdotool key --clearmodifiers ctrl+a", script)
        self.assertIn("timeout 3s xclip -o -selection clipboard", script)
        self.assertNotIn("ctrl+l", script)


if __name__ == "__main__":
    unittest.main()
