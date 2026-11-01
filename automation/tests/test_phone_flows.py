"""Phone flows against scripted screens: the hybrid route, the publish rules and the first comment."""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(1, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from phone import screen  # noqa: E402
from phone.flows import (AccountBlocked, Flows, PostItem, PublishUncertain, StepFailed)  # noqa: E402
from phone import job  # noqa: E402
from phone.screen import Line  # noqa: E402


def lines(*items):
    """("text", y) pairs as OCR lines at x 100."""
    return [Line(text, (100.0, float(y))) for text, y in items]


class FakeDevice:
    """Screens come from `screens` in order (the last one repeats); time is virtual."""

    def __init__(self, screens=None):
        self.screens = list(screens or [[]])
        self.t = 0.0
        self.actions = []
        self.icons = {}
        self.deleted = []
        self.width = 576
        self.keyboard = []  # answers to keyboard_shown, in order; then False

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds

    async def settle(self, quiet=0.6, timeout=5.0):
        self.t += 0.5

    async def read(self):
        self.t += 0.3
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    def find_icon(self, name, below=None):
        point = self.icons.get(name)
        return point if point and (below is None or point[1] > below) else None

    def keyboard_shown(self):
        return self.keyboard.pop(0) if self.keyboard else False

    def snapshot(self, name):
        self.actions.append(("snapshot", name))
        return f"/evidence/{name}.png"

    def pt(self, x, y):
        return x * self.width / 1080, y * self.width / 1080

    async def tap(self, point):
        self.actions.append(("tap", point))
        self.t += 1

    async def swipe(self, start, end):
        self.actions.append(("swipe", start, end))
        self.t += 1

    async def key(self, code):
        self.actions.append(("key", code))

    async def paste(self, text):
        self.actions.append(("paste", text))

    def put_media(self, path, name):
        return "content://agent/media/1"

    def put_media_shell(self, path, name):
        return "content://media/external/images/media/9"

    def delete_media(self, uri):
        self.deleted.append(uri)

    def launch_facebook(self):
        self.actions.append(("intent", "launch"))

    def open_profile_link(self):
        self.actions.append(("intent", "profile"))


def run(coroutine):
    return asyncio.run(coroutine)


class ScreenTests(unittest.TestCase):
    def test_long_text_matches_ignoring_spaces_and_punctuation(self):
        found = lines(("Morningcoffee, test#3 by the river", 400))
        self.assertEqual(screen.match(found, "Morning coffee test #3"), (100.0, 400.0))
        self.assertIsNone(screen.match(found, "Morning", exact=True))

    def test_post_ages(self):
        self.assertEqual(screen.post_age_minutes("Just now ·"), 0)
        self.assertEqual(screen.post_age_minutes("7m"), 7)
        self.assertEqual(screen.post_age_minutes("2h ·"), 120)
        self.assertEqual(screen.post_age_minutes("Yesterday"), 24 * 60)
        self.assertIsNone(screen.post_age_minutes("Morning coffee"))

    def test_caption_key_keeps_readable_english_only(self):
        self.assertEqual(screen.caption_key("Sunset at the beach tonight 🌅"), "Sunset at the beach ")
        self.assertEqual(screen.caption_key("ព្រះអាទិត្យលិច 🌅"), "")

    def test_both_discard_prompts(self):
        self.assertEqual(screen.discard_button(lines(("Discard changes?", 500), ("DISCARD", 700))), (100.0, 700.0))
        self.assertEqual(screen.discard_button(lines(("Want to finish your post later?", 500),
                                                     ("Discard post", 800))), (100.0, 800.0))
        self.assertIsNone(screen.discard_button(lines(("DISCARD", 700))))

    def test_permission_prompt_is_denied(self):
        kind, action, point = screen.interruption(lines(("Allow Facebook to send you notifications?", 500),
                                                        ("Allow", 700), ("Don't allow", 800)))
        self.assertEqual((kind, action, point), ("permission prompt", "tap", (100.0, 800.0)))

    def test_logged_out(self):
        self.assertTrue(screen.logged_out(lines(("Log in", 900), ("Create new account", 1100))))


class HybridRouteTests(unittest.TestCase):
    def test_shortcut_runs_once_after_the_human_route_fails_and_is_logged(self):
        events = []
        flows = Flows(FakeDevice(), lambda **e: events.append(e))

        async def human():
            raise StepFailed("app drawer: the Facebook icon was not found")

        async def shortcut():
            return "ok"
        self.assertEqual(run(flows.hybrid("open_facebook", human, shortcut)), "ok")
        self.assertEqual(flows.fallbacks[0]["step"], "open_facebook")
        self.assertTrue(flows.fallbacks[0]["worked"])
        self.assertTrue(flows.fallbacks[0]["frame"].endswith(".png"))
        self.assertEqual(events[0]["event"], "fallback")
        # The same step does not fall back twice in one job.
        with self.assertRaises(StepFailed):
            run(flows.hybrid("open_facebook", human, shortcut))

    def test_human_only_never_uses_the_shortcut(self):
        flows = Flows(FakeDevice(), lambda **e: None, human_only=True)
        used = []

        async def human():
            raise StepFailed("no")

        async def shortcut():
            used.append(True)
        with self.assertRaises(StepFailed):
            run(flows.hybrid("open_profile", human, shortcut))
        self.assertEqual(used, [])

    def test_account_problems_never_fall_back(self):
        device = FakeDevice([lines(("Log in", 900), ("Create new account", 1100))])
        flows = Flows(device, lambda **e: None)
        used = []

        async def shortcut():
            used.append(True)
        with self.assertRaises(AccountBlocked):
            run(flows.hybrid("open_facebook", lambda: flows.wait_for("feed", ["What's on your mind?"]), shortcut))
        self.assertEqual(used, [])


class PublishTests(unittest.TestCase):
    def test_unconfirmed_publish_is_uncertain(self):
        device = FakeDevice([lines(("Posting... Keep Facebook open.", 300))])
        flows = Flows(device, lambda **e: None)
        with self.assertRaises(PublishUncertain):
            run(flows.wait_published(PostItem("photo", "Morning coffee test"), tapped=0.0))

    def test_photo_is_confirmed_by_caption_and_just_now(self):
        device = FakeDevice([lines(("Posting...", 300)),
                             lines(("What's on your mind?", 200), ("Just now", 400), ("Morning coffee test", 500))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("photo", "Morning coffee test"), 0.0))
        self.assertLess(took, 5)

    def test_record_is_written_before_the_publish_tap(self):
        device = FakeDevice([lines(("Next", 50)), lines(("Post settings", 60)), lines(("Post settings", 60), ("SHARE", 60))])
        order = []
        original = device.tap

        async def tap(point):
            order.append(("tap", point))
            await original(point)
        device.tap = tap
        run(Flows(device, lambda **e: None).publish(lambda: order.append("recorded")))
        self.assertEqual(order[-2:], ["recorded", ("tap", (100.0, 60.0))])


class FindPostTests(unittest.TestCase):
    def test_skips_pinned_post_and_finds_ours(self):
        device = FakeDevice([lines(("Pinned post", 300), ("3d", 340), ("Morning coffee test", 450))])
        device.icons["comment"] = (60.0, 600.0)
        device.screens.insert(1, lines(("Write a comment...", 1100)))
        flows = Flows(device, lambda **e: None)
        self.assertTrue(run(flows.scan_profile(PostItem("photo", "Morning coffee test"), 3, 1)))
        self.assertIn(("tap", (60.0, 600.0)), device.actions)

    def test_an_older_unpinned_post_ends_the_look(self):
        device = FakeDevice([lines(("Pinned post", 300), ("3d", 340), ("Old caption", 700), ("2h", 760))])
        events = []
        flows = Flows(device, lambda **e: events.append(e))
        self.assertFalse(run(flows.scan_profile(PostItem("photo", "Morning coffee test"), 3, 1)))
        self.assertEqual(events[-1]["reason"], "reached a post 120 min old")

    def test_three_looks_then_skip(self):
        device = FakeDevice()
        flows = Flows(device, lambda **e: None)
        looks = []

        async def nothing(item, allowed, attempt):
            looks.append((attempt, device.t))
            return False

        async def noop():
            return None
        with patch.object(flows, "scan_profile", nothing), patch.object(flows, "open_feed", noop), \
                patch.object(flows, "open_profile", noop), patch.object(flows, "refresh_profile", noop):
            with self.assertRaises(StepFailed):
                run(flows.find_and_comment(PostItem("reel", "Clip", "hi"), published_at=0.0))
        self.assertEqual([attempt for attempt, _ in looks], [1, 2, 3])
        self.assertEqual([round(t) for _, t in looks], [30, 55, 80])


class KeyboardTests(unittest.TestCase):
    def test_scroll_hides_the_keyboard_and_closes_the_sheet_first(self):
        device = FakeDevice()
        device.keyboard = [True, False]
        run(Flows(device, lambda **e: None).scroll((10, 900), (10, 300)))
        kinds = [action[0] if action[0] != "key" else ("key", action[1]) for action in device.actions]
        self.assertEqual(kinds, [("key", 111), ("key", 4), "swipe"])

    def test_scroll_refuses_while_the_keyboard_stays_up(self):
        device = FakeDevice()
        device.keyboard = [True, True]
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).scroll((10, 900), (10, 300)))
        self.assertNotIn("swipe", [action[0] for action in device.actions])

    def test_comment_is_not_sent_when_the_box_shows_other_text(self):
        device = FakeDevice([lines(("Comment as Eliza", 1200)), lines(("By see see see", 1200))])
        device.icons["send"] = (500.0, 1200.0)
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).write_comment(PostItem("reel", "Clip", "share our thought")))
        self.assertNotIn(("tap", (500.0, 1200.0)), device.actions)

    def test_empty_comment_sheet_is_recognised(self):
        device = FakeDevice([lines(("Get the ball rolling", 400), ("Comment as Eliza", 1200)),
                             lines(("share our thought", 1200)), lines(("share our thought", 600))])
        device.icons["send"] = (500.0, 1200.0)
        run(Flows(device, lambda **e: None).write_comment(PostItem("reel", "Clip", "share our thought")))
        self.assertIn(("tap", (500.0, 1200.0)), device.actions)


class GetReadyTests(unittest.TestCase):
    def test_consent_is_allowed_and_the_composer_left_without_posting(self):
        device = FakeDevice([lines(("What's on your mind?", 300)), lines(("Gallery", 900)),
                             lines(("Allow access to your camera roll", 400), ("Allow", 1100)),
                             lines(("Gallery", 200))])
        events = []
        flows = Flows(device, lambda **e: events.append(e), human_only=True)

        async def ok():
            return None
        with patch.object(flows, "open_feed", ok), patch.object(flows, "discard_until_feed", ok):
            run(flows.get_ready())
        kinds = [e["event"] for e in events]
        self.assertIn("gallery_ready", kinds)
        self.assertIn("camera roll consent", [e.get("kind") for e in events])
        self.assertIn(("key", 4), device.actions)
        self.assertNotIn("about_to_publish", kinds)

    def test_runner_result_is_ready_or_failed(self):
        async def ready(self_):
            return None

        async def broken(self_):
            raise StepFailed("picker: still asking for camera roll access")
        with patch.object(Flows, "get_ready", ready):
            self.assertEqual(run(job.run_get_ready(FakeDevice(), lambda **e: None))["status"], "ready")
        with patch.object(Flows, "get_ready", broken):
            result = run(job.run_get_ready(FakeDevice(), lambda **e: None))
        self.assertEqual(result["status"], "failed")
        self.assertIn("camera roll", result["reason"])


class JobTests(unittest.TestCase):
    def run_job(self, publish=None, wait=None, start=None, comment=None):
        device = FakeDevice()
        events = []

        async def ok(*args, **kwargs):
            return None

        async def tapped(self_, before_tap):
            before_tap()
            return 10.0

        async def published(self_, item, at):
            return 12.0
        patches = [patch.object(Flows, "open_feed", ok), patch.object(Flows, "discard_until_feed", ok),
                   patch.object(Flows, "start_post", start or ok), patch.object(Flows, "publish", publish or tapped),
                   patch.object(Flows, "wait_published", wait or published),
                   patch.object(Flows, "find_and_comment", comment or (lambda *a, **k: ok()))]
        for item in patches:
            item.start()
        try:
            with patch.object(job, "media_mime", lambda path: "image/jpeg"):
                result = run(job.run_post(device, PostItem("photo", "Morning coffee test", "First!"),
                                          "/media/a.jpg", lambda **e: events.append(e)))
        finally:
            for item in patches:
                item.stop()
        return result, device, events

    def test_published_with_comment(self):
        async def commented(self_, item, at, waits, max_age):
            return 2
        result, device, _ = self.run_job(comment=commented)
        self.assertEqual((result["status"], result["comment"], result["comment_looks"]), ("published", "posted", 2))
        self.assertEqual(device.deleted, ["content://agent/media/1"])

    def test_failure_before_the_tap_is_a_plain_failure(self):
        async def broken(self_, item, uri, mime):
            raise StepFailed("composer: none of ['Gallery'] appeared")
        result, device, _ = self.run_job(start=broken)
        self.assertEqual((result["status"], result["stage"]), ("failed", "compose"))
        self.assertEqual(device.deleted, ["content://agent/media/1"])

    def test_lost_stream_after_the_tap_is_uncertain_and_keeps_the_media(self):
        async def dropped(self_, item, at):
            raise ConnectionResetError("stream closed")
        result, device, _ = self.run_job(wait=dropped)
        self.assertEqual((result["status"], result["stage"]), ("uncertain", "confirm"))
        self.assertEqual(device.deleted, [])
        self.assertEqual(result["media_left"], "content://agent/media/1")

    def test_logged_out_before_the_tap_blocks_the_account(self):
        async def logged_out(self_, item, uri, mime):
            raise AccountBlocked("Facebook shows its login screen")
        result, _, _ = self.run_job(start=logged_out)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["account_blocked"])

    def test_skipped_comment_keeps_the_post_published(self):
        async def missing(self_, item, at, waits, max_age):
            raise StepFailed("comment skipped: the post did not show on the profile after 3 looks")
        result, _, events = self.run_job(comment=missing)
        self.assertEqual((result["status"], result["comment"]), ("published", "skipped"))
        self.assertIn("comment_skipped", [e["event"] for e in events])


if __name__ == "__main__":
    unittest.main()
