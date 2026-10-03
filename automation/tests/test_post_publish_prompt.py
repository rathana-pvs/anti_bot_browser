import unittest
from unittest.mock import Mock, patch

from engine.post_publish_prompt import PostPublishPromptHandler


class PostPublishPromptHandlerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
