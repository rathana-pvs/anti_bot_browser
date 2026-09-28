"""Focused tests for Chrome window discovery."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.container_client import ContainerClient


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class ChromeWindowDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = ContainerClient("test-profile")
        self.client.exec_cmd = Mock()

    def test_returns_largest_visible_chrome_window(self):
        self.client.exec_cmd.side_effect = [
            completed(stdout="101\n202\n"),
            completed(stdout="Geometry: 800x600\n"),
            completed(stdout="Geometry: 1280x720\n"),
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


if __name__ == "__main__":
    unittest.main()
