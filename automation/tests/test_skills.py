"""Shared skills against a fake host: no browser, Docker or OCR runtime."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skills.dialogs import KnownDialogs


def fake_host(**overrides):
    host = SimpleNamespace(
        client=Mock(),
        human=Mock(),
        vision=Mock(),
        recognizer=Mock(),
        log=Mock(),
        capture_evidence=Mock(),
        telemetry=None,
    )
    host.client.screenshot.return_value = np.zeros((1000, 2000, 3), dtype=np.uint8)
    for name, value in overrides.items():
        setattr(host, name, value)
    return host


class KnownDialogsTests(unittest.TestCase):
    def test_leave_site_clicks_leave_through_human_input(self):
        host = fake_host()
        host.vision.find_leave_site_button.return_value = (640, 410)
        with patch("skills.dialogs.time.sleep"):
            self.assertTrue(KnownDialogs(host).leave_site())
        host.human.click.assert_called_once_with(640, 410)
        host.client.dismiss_dialog_key.assert_not_called()

    def test_leave_site_absent_does_nothing(self):
        host = fake_host()
        host.vision.find_leave_site_button.return_value = None
        self.assertFalse(KnownDialogs(host).leave_site())
        host.human.click.assert_not_called()

    def test_post_prompt_picks_the_most_confident_not_now(self):
        host = fake_host()
        host.vision.read_text.return_value = [
            {"text": "Not now", "confidence": 0.6, "center": (900, 700)},
            {"text": "Share now", "confidence": 0.99, "center": (900, 600)},
            {"text": "not n0w", "confidence": 0.9, "center": (910, 710)},
        ]
        match = KnownDialogs(host).find_post_publish_prompt()
        self.assertEqual(match["center"], (910, 710))
        region = host.vision.read_text.call_args.kwargs["region"]
        self.assertEqual(region, (300, 80, 1400, 870))

    def test_dismiss_post_prompt_uses_the_host_locator_and_reversible_click(self):
        host = fake_host(
            find_post_publish_prompt=Mock(return_value={"center": (850, 800), "bounds": (800, 780, 900, 820)}),
            click_reversible=Mock(),
        )
        with patch("skills.dialogs.time.sleep"):
            self.assertTrue(KnownDialogs(host).dismiss_post_prompt())
        host.click_reversible.assert_called_once_with(
            (850, 800), label="dismiss_post_prompt", bounds=(800, 780, 900, 820), max_offset_px=4,
        )

    def test_post_publish_prompt_prefers_the_shared_handler(self):
        handler = Mock()
        handler.handle.return_value = "dismissed"
        host = fake_host(post_publish_prompt=handler, check_and_dismiss_post_prompt=Mock())
        self.assertEqual(KnownDialogs(host).handle_post_publish_prompt(), "dismissed")
        host.check_and_dismiss_post_prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
