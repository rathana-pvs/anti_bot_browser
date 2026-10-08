import unittest
from unittest.mock import Mock, patch

from engine.post_publish_prompt import PostPublishPromptHandler


class PostPublishPromptHandlerTests(unittest.TestCase):
    def test_own_phase_timeout_stops_without_clicking_again(self):
        task = self.make_task([{"center": (100, 200)}])
        with patch("engine.post_publish_prompt.time.monotonic", return_value=10):
            self.assertEqual(PostPublishPromptHandler(task, timeout=0).handle(object()), "failed")
        task.click_reversible.assert_not_called()
        task.telemetry.record_step.assert_called_once()

    def make_task(self, matches):
        task = Mock()
        task.client.screenshot.return_value = object()
        task.find_post_publish_prompt.side_effect = matches
        return task

    def test_absent_prompt_continues_without_click(self):
        task = self.make_task([None])
        result = PostPublishPromptHandler(task).handle(object())
        self.assertEqual(result, "absent")
        task.click_reversible.assert_not_called()

    def test_present_prompt_is_clicked_once_and_confirmed_closed(self):
        match = {"center": (100, 200), "bounds": (80, 180, 120, 220)}
        task = self.make_task([match, None])
        handler = PostPublishPromptHandler(task, close_timeout=1, poll_interval=0)
        with patch("engine.post_publish_prompt.time.sleep"):
            result = handler.handle(object())
            second = handler.handle(object())

        self.assertEqual(result, "dismissed")
        self.assertEqual(second, "dismissed")
        task.click_reversible.assert_called_once_with(
            (100, 200), label="dismiss_post_prompt",
            bounds=(80, 180, 120, 220), max_offset_px=4,
        )

    def test_retries_with_buffer_and_fresh_target_then_confirms_closed(self):
        first = {"center": (100, 200)}
        second = {"center": (110, 210)}
        third = {"center": (120, 220)}
        task = self.make_task([first, second, third, None])
        events = []
        task.click_reversible.side_effect = lambda *a, **k: events.append("click")
        task.client.screenshot.side_effect = lambda: events.append("verify") or object()
        handler = PostPublishPromptHandler(task, close_timeout=0)
        with patch("engine.post_publish_prompt.time.sleep", side_effect=lambda s: events.append(("buffer", s))):
            self.assertEqual(handler.handle(object()), "dismissed")
        self.assertEqual(events, ["click", ("buffer", 5.0), "verify"] * 3)
        self.assertEqual([c.args[0] for c in task.click_reversible.call_args_list],
                         [(100, 200), (110, 210), (120, 220)])

    def test_persistent_prompt_fails_after_exactly_three_attempts(self):
        match = {"center": (100, 200)}
        task = self.make_task([match] * 4)
        handler = PostPublishPromptHandler(task, close_timeout=0)
        with patch("engine.post_publish_prompt.time.sleep") as sleep:
            self.assertEqual(handler.handle(object()), "failed")
        self.assertEqual(task.click_reversible.call_count, 3)
        self.assertEqual(task.client.screenshot.call_count, 3)
        self.assertEqual(sleep.call_count, 3)
        self.assertFalse(handler.dismissed)
        self.assertEqual(handler.last_status, "failed")

    def test_stops_retrying_after_second_attempt_succeeds(self):
        match = {"center": (100, 200)}
        task = self.make_task([match, match, None])
        handler = PostPublishPromptHandler(task, close_timeout=0)
        with patch("engine.post_publish_prompt.time.sleep"):
            self.assertEqual(handler.handle(object()), "dismissed")
        self.assertEqual(task.click_reversible.call_count, 2)


if __name__ == "__main__":
    unittest.main()
