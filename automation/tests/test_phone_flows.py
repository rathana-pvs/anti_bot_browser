"""Phone flows against scripted screens: reaching the feed, the publish rules and the first comment."""

import asyncio
import os
import sys
import tempfile
import unittest

import numpy as np
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(1, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from phone import screen  # noqa: E402
from phone.flows import (AccountBlocked, Flows, NotAReel, PostItem, PublishUncertain, StepFailed)  # noqa: E402
from phone import device as phone_device, job  # noqa: E402
from phone import flows as flows_module  # noqa: E402
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
        self.images = []
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

    def image(self):
        return self.images.pop(0) if len(self.images) > 1 else (self.images[0] if self.images else None)

    def snapshot(self, name):
        self.actions.append(("snapshot", name))
        return f"/evidence/{name}.png"

    def pt(self, x, y):
        return x * self.width / 1080, y * self.width / 1080

    async def tap(self, point):
        self.actions.append(("tap", point))
        self.t += 1

    async def swipe(self, start, end, duration=0.35):
        self.actions.append(("swipe", start, end))
        self.t += 1

    async def key(self, code):
        self.actions.append(("key", code))

    async def paste(self, text):
        self.actions.append(("paste", text))

    def put_media(self, path, name):
        return "content://agent/media/1"

    def delete_media(self, uri):
        self.deleted.append(uri)


class ProfileWithCards(FakeDevice):
    """A profile whose Posts tab shows only after enough swipes (`None`: never)."""

    def __init__(self, swipes_to_tabs):
        super().__init__([lines(("What's on your mind?", 300))])
        self.swipes_to_tabs = swipes_to_tabs
        self.reads = 0

    async def read(self):
        self.t += 0.3
        self.reads += 1
        if self.reads == 1:
            return self.screens[0]
        swipes = sum(action[0] == "swipe" for action in self.actions)
        if self.swipes_to_tabs is not None and swipes >= self.swipes_to_tabs:
            return lines(("Posts", 900))
        return lines(("0 followers", 300), ("Get more out of your profile", 550))


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

    def test_unfinished_post_prompts_are_discarded(self):
        self.assertEqual(screen.discard_button(lines(("Continue editing your last post?", 500), ("CONTINUE EDITING", 640),
                                                     ("DISCARD", 800))), (100.0, 800.0))
        self.assertEqual(screen.discard_button(lines(("Continue editing your last reel?", 500), ("Discard", 800))), (100.0, 800.0))
        self.assertEqual(screen.discard_button(lines(("Save your draft?", 500), ("Discard draft", 800))), (100.0, 800.0))

    def test_reel_draft_sheet_is_discarded(self):
        self.assertEqual(screen.discard_button(lines(("Save this reel as a draft?", 500), ("Save as draft", 600),
                                                     ("Keep editing", 700), ("Discard video", 800))), (100.0, 800.0))

    def test_caption_tail_is_the_end_of_the_last_line(self):
        self.assertEqual(screen.caption_tail_key("Long story here\nMore detail in the comment 👇"), "etail in the comment")
        self.assertEqual(screen.caption_tail_key("ព្រះអាទិត្យលិច 🌅"), "")

    def test_permission_prompt_is_denied(self):
        kind, action, point = screen.interruption(lines(("Allow Facebook to send you notifications?", 500),
                                                        ("Allow", 700), ("Don't allow", 800)))
        self.assertEqual((kind, action, point), ("permission prompt", "tap", (100.0, 800.0)))

    def test_logged_out(self):
        self.assertTrue(screen.logged_out(lines(("Log in", 900), ("Create new account", 1100))))


class FeedTests(unittest.TestCase):
    def test_facebook_opens_by_tapping_the_icon_ocr_found(self):
        device = FakeDevice([lines(("Facebook", 500)), lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).open_feed())
        self.assertIn(("tap", (100.0, 500.0 - device.pt(0, 110)[1])), device.actions)

    def test_a_scrolled_feed_taps_home_before_swiping(self):
        device = FakeDevice([lines(("facebook", 100)),
                             lines(("facebook", 100), ("What's on your mind?", 200))])
        device.icons["home_tab"] = (48.0, 1211.0)
        run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertEqual(device.actions, [("tap", (48.0, 1211.0))])

    def test_home_that_does_not_reach_the_composer_falls_back_to_swiping(self):
        device = FakeDevice([lines(("facebook", 100)), lines(("facebook", 100)),
                             lines(("facebook", 100), ("What's on your mind?", 200))])
        device.icons["home_tab"] = (48.0, 1211.0)
        run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertEqual([action[0] for action in device.actions], ["tap", "swipe", "swipe"])

    def test_start_post_recovers_the_scrolled_feed_before_opening_gallery(self):
        for kind in ("photo", "reel"):
            with self.subTest(kind=kind):
                feed = lines(("facebook", 100), ("What's on your mind?", 200))
                device = FakeDevice([lines(("facebook", 100)), feed, feed,
                                     lines(("Gallery", 900)), lines(("Gallery", 200))])
                device.icons["home_tab"] = (48.0, 1211.0)
                flows = Flows(device, lambda **e: None)

                async def compose():
                    await flows.open_gallery()

                async def caption(item):
                    pass

                with patch.object(flows, "_compose_from_gallery", compose), \
                        patch.object(flows, "write_caption", caption):
                    run(flows.start_post(PostItem(kind, "Clip")))
                self.assertEqual(device.actions[:2], [("tap", (48.0, 1211.0)), ("tap", (100.0, 200.0))])

    def test_start_post_does_not_compose_if_home_and_swipes_cannot_recover_feed(self):
        device = FakeDevice([lines(("facebook", 100))])
        device.icons["home_tab"] = (48.0, 1211.0)
        flows = Flows(device, lambda **e: None)
        with patch.object(flows, "_compose_from_gallery") as compose, self.assertRaises(StepFailed):
            run(flows.start_post(PostItem("reel", "Clip")))
        compose.assert_not_called()

    def test_the_last_try_reaching_the_top_counts(self):
        device = FakeDevice([lines(("facebook", 100))] * 8 + [lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).discard_until_feed())

    def test_a_left_over_reel_is_discarded_on_the_way_to_the_feed(self):
        device = FakeDevice([lines(("New reel", 50), ("Share now", 1200)), lines(("Audio", 400)),
                             lines(("Save this reel as a draft?", 700), ("Discard video", 900)),
                             lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertIn(("tap", (100.0, 900.0)), device.actions)
        self.assertEqual([action for action in device.actions if action[0] == "tap"], [("tap", (100.0, 900.0))])

    def test_a_feed_never_reaching_the_top_fails_with_a_frame(self):
        device = FakeDevice([lines(("facebook", 100))])
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertTrue(any(action[0] == "snapshot" for action in device.actions))

    def test_account_problems_stop_the_step(self):
        device = FakeDevice([lines(("Log in", 900), ("Create new account", 1100))])
        with self.assertRaises(AccountBlocked):
            run(Flows(device, lambda **e: None).wait_for("feed", ["What's on your mind?"]))


class ApprovalScreenTests(unittest.TestCase):
    APPROVAL = "com.facebook.katana/com.facebook.fxcal.approvefromanotherdevice.BloksApproveFromAnotherDeviceActivity"
    FEED = "com.facebook.katana/com.facebook.katana.LoginActivity"

    def real_device(self, focus):
        class Client:
            def focused_window(self, serial):
                return focus.pop(0) if len(focus) > 1 else focus[0]

        class Session:
            sent = []

            async def send(self, message):
                self.sent.append(message)
        folder = tempfile.mkdtemp()
        device = phone_device.PhoneDevice("S", 12, phone_device.Path(folder), lambda **e: None, client=Client())
        device.session, device.width, device.height = Session(), 576, 1280
        Session.sent = []
        return device

    def quickly(self):
        real_sleep = asyncio.sleep

        async def no_wait(seconds):
            await real_sleep(0)
        return patch.object(phone_device.asyncio, "sleep", no_wait)

    def test_input_waits_until_the_screen_closes(self):
        device = self.real_device([self.APPROVAL, self.APPROVAL, self.FEED])
        with self.quickly():
            run(device.tap((100, 200)))
        self.assertEqual(len(device.session.sent), 2)

    def test_nothing_is_sent_while_the_screen_stays_up(self):
        for act in (lambda d: d.tap((100, 200)), lambda d: d.swipe((1, 2), (3, 4)), lambda d: d.key(4)):
            device = self.real_device([self.APPROVAL])
            with self.quickly(), patch.object(phone_device, "APPROVAL_WAIT_S", 0.01):
                with self.assertRaises(phone_device.ApprovalScreenUp):
                    run(act(device))
            self.assertEqual(device.session.sent, [])

    def test_input_goes_through_on_other_screens(self):
        device = self.real_device([self.FEED])
        with self.quickly():
            run(device.key(4))
        self.assertEqual(device.session.sent, [{"type": "key", "keycode": 4}])


class PublishTests(unittest.TestCase):
    def test_unconfirmed_publish_is_uncertain(self):
        device = FakeDevice([lines(("Posting... Keep Facebook open.", 300))])
        flows = Flows(device, lambda **e: None)
        with self.assertRaises(PublishUncertain):
            run(flows.wait_published(PostItem("photo", "Morning coffee test"), tapped=0.0))

    def test_photo_is_confirmed_by_caption_and_just_now(self):
        device = FakeDevice([lines(("Posting...", 300)),
                             lines(("facebook", 100), ("What's on your mind?", 200), ("Just now", 400),
                                   ("Morning coffee test", 500))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("photo", "Morning coffee test"), 0.0))
        self.assertLess(took, 5)

    def test_long_caption_scrolled_to_its_end_counts_as_shown(self):
        caption = "A very long story about many things\nMore detail in the comment 👇"
        device = FakeDevice([lines(("Describe your reel", 300)), lines(("New reel", 50), ("is...See more", 300),
                                                                       ("More detail in the comment", 330))])
        run(Flows(device, lambda **e: None).write_caption(PostItem("reel", caption)))

    def test_caption_end_wrapping_onto_two_lines_counts_as_shown(self):
        caption = "A very long story about many things\n#DonaldTrump #USPolitics #Cuba #MarcoRubio"
        device = FakeDevice([lines(("Describe your reel", 300)),
                             lines(("New reel", 50), ("#MarcoRubio", 330), ("#DonaldTrump #USPolitics #Cuba", 300))])
        run(Flows(device, lambda **e: None).write_caption(PostItem("reel", caption)))

    def test_caption_nowhere_on_screen_still_fails(self):
        caption = "A very long story about many things\nMore detail in the comment 👇"
        device = FakeDevice([lines(("Describe your reel", 300)), lines(("Describe your reel", 300))])
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).write_caption(PostItem("reel", caption)))

    def test_new_reel_screen_goes_straight_to_share_now(self):
        device = FakeDevice([lines(("New reel", 50), ("Share now", 1200))])
        order = []
        original = device.tap

        async def tap(point):
            order.append(("tap", point))
            await original(point)
        device.tap = tap
        events = []
        run(Flows(device, lambda **e: events.append(e)).publish(lambda: order.append("recorded")))
        self.assertEqual(order, ["recorded", ("tap", (100.0, 1200.0))])
        self.assertEqual(events[-1]["event"], "about_to_publish")

    def test_empty_composer_after_a_reel_is_not_the_feed(self):
        composer = lines(("< Create post", 100), ("What's on your mind?", 400))
        device = FakeDevice([composer])
        with self.assertRaises(PublishUncertain):
            run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertIn(("key", 4), device.actions)

    def test_reel_is_confirmed_on_the_feed_after_leaving_the_composer(self):
        device = FakeDevice([lines(("< Create post", 100), ("What's on your mind?", 400)),
                             lines(("facebook", 100), ("What's on your mind?", 200), ("Posting...", 300)),
                             lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertEqual([a for a in device.actions if a[0] != "snapshot"], [("key", 4)])

    def test_reel_is_not_confirmed_when_the_upload_bar_blinks_away(self):
        feed = lines(("facebook", 100), ("What's on your mind?", 200))
        bar = lines(("facebook", 100), ("Posting...", 300))
        device = FakeDevice([bar, feed, feed, bar, bar, feed])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertGreater(took, 20)

    def test_long_upload_keeps_waiting_past_the_confirm_limit(self):
        device = FakeDevice([lines(("Posting...", 300))] * 200 + [lines(("facebook", 100))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertGreater(took, 300)

    def test_upload_that_never_ends_stays_uncertain(self):
        device = FakeDevice([lines(("Posting...", 300))])
        flows = Flows(device, lambda **e: None)
        with self.assertRaises(PublishUncertain):
            run(flows.wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertGreater(device.now(), 880)

    def test_feed_post_mentioning_posting_is_not_the_upload_bar(self):
        device = FakeDevice([lines(("facebook", 100), ("Posting this photo from my trip to the mountains last week", 500))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertLess(took, 65)

    def test_feed_without_the_prompt_in_view_confirms_a_reel(self):
        device = FakeDevice([lines(("facebook", 100), ("Create story", 400))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertGreater(took, 60)
        self.assertLess(took, 65)

    def test_profile_after_create_reel_confirms_a_reel(self):
        device = FakeDevice([lines(("66K followers · 16 following", 640), ("Create reel", 800))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertGreater(took, 60)
        self.assertLess(took, 65)

    def test_reels_tab_after_create_reel_confirms_a_reel(self):
        device = FakeDevice([lines(("Reels", 200), ("Create reel", 520))])
        took = run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))
        self.assertLess(took, 65)

    def test_create_reel_with_share_now_still_up_is_not_confirmed(self):
        device = FakeDevice([lines(("Create reel", 520), ("Share now", 1200))])
        with self.assertRaises(PublishUncertain):
            run(Flows(device, lambda **e: None).wait_published(PostItem("reel", "Clip"), 0.0))

    def test_profile_does_not_confirm_a_photo(self):
        device = FakeDevice([lines(("66K followers · 16 following", 640))])
        with self.assertRaises(PublishUncertain):
            run(Flows(device, lambda **e: None).wait_published(PostItem("photo", "Morning coffee test"), 0.0))

    def test_no_home_tab_found_keeps_swiping(self):
        device = FakeDevice([lines(("facebook", 100)), lines(("facebook", 100)),
                             lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertEqual([action[0] for action in device.actions], ["swipe"] * 4)

    def test_page_profile_opens_on_its_all_tab(self):
        device = FakeDevice([lines(("What's on your mind?", 300)), lines(("All", 1100), ("Photos", 1100))])
        run(Flows(device, lambda **e: None).open_profile())
        self.assertIn(("tap", (100.0, 1100.0)), device.actions)

    def test_profile_scrolls_past_promo_cards_to_its_tabs(self):
        device = ProfileWithCards(swipes_to_tabs=3)
        run(Flows(device, lambda **e: None).open_profile())
        self.assertEqual(sum(action[0] == "swipe" for action in device.actions), 3)
        self.assertEqual(device.actions[-1], ("tap", (100.0, 900.0)))

    def test_profile_without_tabs_fails_after_its_scrolls(self):
        device = ProfileWithCards(swipes_to_tabs=None)
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).open_profile())
        self.assertEqual(sum(action[0] == "swipe" for action in device.actions), 6)
        self.assertTrue(any(action[0] == "snapshot" and action[1].startswith("fail_profile")
                            for action in device.actions))

    def test_page_photo_composer_goes_straight_to_post(self):
        device = FakeDevice([lines(("Create post", 50), ("POST", 60), ("court", 300))])
        order = []
        original = device.tap

        async def tap(point):
            order.append(("tap", point))
            await original(point)
        device.tap = tap
        run(Flows(device, lambda **e: None).publish(lambda: order.append("recorded")))
        self.assertEqual(order, ["recorded", ("tap", (100.0, 60.0))])

    def test_settings_with_a_post_button_publishes_with_it(self):
        settings = lines(("New post", 90), ("Post audience", 820), ("Post", 1210))
        device = FakeDevice([lines(("Next", 50)), settings])
        order = []
        run(Flows(device, lambda **e: None).publish(lambda: order.append("recorded")))
        self.assertEqual(device.actions[-1], ("tap", (100.0, 1210.0)))
        self.assertEqual(order, ["recorded"])

    @staticmethod
    def frame(colour):
        return np.full((1280, 576, 3), colour, dtype=np.uint8)

    def test_greyed_out_button_is_waited_for_before_the_tap(self):
        device = FakeDevice([lines(("Share now", 1200))])
        grey, blue = self.frame((232, 227, 226)), self.frame((255, 101, 8))
        device.images = [grey] * 5 + [blue]  # three scrolls, then two plain waits
        order = []
        run(Flows(device, lambda **e: None).publish(lambda: order.append(device.t)))
        self.assertEqual(len([a for a in device.actions if a[0] == "swipe"]), 3)
        self.assertGreaterEqual(order[0], 6)
        self.assertEqual(device.actions[-1], ("tap", (100.0, 1200.0)))

    def test_grey_share_now_turns_blue_after_the_settings_list_is_scrolled(self):
        settings = lines(("Reel Settings", 90), ("Location", 410), ("Share now", 1200))
        device = FakeDevice([lines(("Share now", 1200)), settings])
        device.images = [self.frame((232, 227, 226)), self.frame((255, 101, 8))]
        order = []
        run(Flows(device, lambda **e: None).publish(lambda: order.append("recorded")))
        swipes = [a for a in device.actions if a[0] == "swipe"]
        self.assertEqual(len(swipes), 1)
        self.assertGreater(swipes[0][1][1], swipes[0][2][1])  # finger moves up: the list scrolls down
        self.assertEqual(device.actions[-1], ("tap", (100.0, 1200.0)))
        self.assertEqual(order, ["recorded"])

    def test_share_now_under_a_toast_is_tapped_only_once_the_toast_is_gone(self):
        toast = lines(("Reel Settings", 90), ("All videos will now be posted as reels and", 1180), ("Learn more", 1190))
        device = FakeDevice([lines(("Share now", 1200)), toast, toast, lines(("Share now", 1205))])
        device.images = [self.frame((40, 40, 40)), self.frame((255, 101, 8))]  # the toast is dark, not grey
        taps_before = []
        run(Flows(device, lambda **e: None).publish(lambda: taps_before.append(
            [a for a in device.actions if a[0] == "tap"])))
        self.assertEqual(taps_before, [[]])
        self.assertEqual(device.actions[-1], ("tap", (100.0, 1205.0)))

    def test_share_now_that_stays_covered_fails_without_a_tap(self):
        toast = lines(("Reel Settings", 90), ("All videos will now be posted as reels and", 1180))
        device = FakeDevice([lines(("Share now", 1200)), toast])
        recorded = []
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).publish(lambda: recorded.append(True)))
        self.assertEqual(recorded, [])
        self.assertNotIn("tap", [a[0] for a in device.actions])

    def test_button_that_stays_grey_fails_without_a_tap(self):
        device = FakeDevice([lines(("Share now", 1200))])
        device.images = [self.frame((232, 227, 226))]
        recorded = []
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None).publish(lambda: recorded.append(True)))
        self.assertEqual(recorded, [])
        self.assertNotIn(("tap", (100.0, 1200.0)), device.actions)

    def test_label_on_a_white_screen_is_not_treated_as_disabled(self):
        device = FakeDevice([lines(("POST", 60))])
        device.images = [self.frame((255, 255, 255))]
        run(Flows(device, lambda **e: None).publish(lambda: None))
        self.assertEqual(device.actions[-1], ("tap", (100.0, 60.0)))

    def test_unfinished_reel_prompt_is_discarded_then_the_video_picked(self):
        dialog = lines(("Gallery", 280), ("Continue editing your last reel?", 500), ("CONTINUE EDITING", 640), ("DISCARD", 800))
        device = FakeDevice([dialog, lines(("Gallery", 280)), lines(("→ Next", 1130)), lines(("Describe your reel", 300))])
        run(Flows(device, lambda **e: None)._pick_reel())
        taps = [a[1] for a in device.actions if a[0] == "tap"]
        offset = device.pt(68, 394)
        self.assertEqual(taps, [(100.0, 800.0), (100.0 + offset[0], 280.0 + offset[1]), (100.0, 1130.0)])

    def test_picker_without_a_gallery_title_picks_the_cell_under_the_recents_chip(self):
        picker = lines(("Create reel", 105), ("Music", 228), ("Drafts", 228), ("Recents", 283), ("Videos", 283))
        device = FakeDevice([picker, lines(("Next", 1208)), lines(("Describe your reel", 300))])
        run(Flows(device, lambda **e: None)._pick_reel())
        taps = [a[1] for a in device.actions if a[0] == "tap"]
        offset = device.pt(75, 290)
        self.assertEqual(taps, [(100.0 + offset[0], 283.0 + offset[1]), (100.0, 1208.0)])

    def test_reel_editor_next_is_tapped_again_when_the_editor_stays(self):
        editor = lines(("Add audio", 90), ("Discover suggestions", 108), ("Next", 1208))
        device = FakeDevice([lines(("Gallery", 280)), lines(("Next", 1208))] + [editor] * 13
                            + [lines(("Describe your reel", 300))])
        run(Flows(device, lambda **e: None)._pick_reel())
        taps = [a[1] for a in device.actions if a[0] == "tap"]
        self.assertEqual(taps[1:], [(100.0, 1208.0), (100.0, 1208.0)])
        self.assertNotIn("snapshot", [a[0] for a in device.actions])

    def test_reel_editor_that_never_advances_fails_after_three_taps(self):
        device = FakeDevice([lines(("Gallery", 280)), lines(("Add audio", 90), ("Next", 1208))])
        with self.assertRaises(StepFailed):
            run(Flows(device, lambda **e: None)._pick_reel())
        taps = [a[1] for a in device.actions if a[0] == "tap"]
        self.assertEqual(taps.count((100.0, 1208.0)), 3)

    def test_unfinished_reel_prompt_appearing_after_the_tap_is_discarded(self):
        dialog = lines(("Gallery", 280), ("Continue editing your last reel?", 500), ("DISCARD", 800))
        device = FakeDevice([lines(("Gallery", 280)), dialog, lines(("Gallery", 280)), lines(("→ Next", 1130)),
                             lines(("Describe your reel", 300))])
        run(Flows(device, lambda **e: None)._pick_reel())
        self.assertIn(("tap", (100.0, 800.0)), device.actions)
        self.assertEqual(device.actions[-1], ("tap", (100.0, 1130.0)))

    def test_leaving_clears_the_unfinished_reel_prompt(self):
        dialog = lines(("Create reel", 90), ("Continue editing your last reel?", 500), ("DISCARD", 800))
        device = FakeDevice([dialog, lines(("facebook", 100), ("What's on your mind?", 200))])
        run(Flows(device, lambda **e: None).discard_until_feed())
        self.assertIn(("tap", (100.0, 800.0)), device.actions)

    def test_video_that_opens_a_plain_post_is_not_posted(self):
        device = FakeDevice([lines(("Say something about this video...", 300), ("POST", 60))])
        flows = Flows(device, lambda **e: None)

        async def gallery():
            return (100.0, 200.0)
        with patch.object(flows, "open_gallery", gallery), self.assertRaises(StepFailed):
            run(flows._compose_from_gallery())
        self.assertNotIn(("tap", (100.0, 60.0)), device.actions)

    def test_video_that_opens_a_plain_post_goes_through_create_reel(self):
        feed = lines(("facebook", 100), ("What's on your mind?", 300))
        device = FakeDevice([feed, feed, feed, lines(("All", 1100), ("Create reel", 800)),
                             lines(("Gallery", 280)), lines(("→ Next", 1130)), lines(("Describe your reel", 300)),
                             lines(("Describe your reel", 300)), lines(("Morning coffee test", 300))])
        flows = Flows(device, lambda **e: None)

        async def plain():
            raise NotAReel("plain video")
        with patch.object(flows, "_compose_from_gallery", plain):
            run(flows.start_post(PostItem("reel", "Morning coffee test")))
        taps = [action[1] for action in device.actions if action[0] == "tap"]
        offset = device.pt(68, 394)
        self.assertEqual(taps[1:4], [(100.0, 800.0), (100.0 + offset[0], 280.0 + offset[1]), (100.0, 1130.0)])
        self.assertIn(("paste", "Morning coffee test"), device.actions)

    def test_reel_goes_through_the_plus_menu(self):
        feed = lines(("facebook", 100), ("What's on your mind?", 300))
        device = FakeDevice([feed, lines(("Create story", 530), ("Post", 158), ("Story", 229), ("B Reel", 297), ("Live", 369)),
                             lines(("Gallery", 280)), lines(("→ Next", 1130)), lines(("Describe your reel", 300)),
                             lines(("Describe your reel", 300)), lines(("Morning coffee test", 300))])
        device.icons["create"] = (426.0, 90.0)
        flows = Flows(device, lambda **e: None)
        with patch.object(flows, "_compose_from_gallery") as gallery:
            run(flows.start_post(PostItem("reel", "Morning coffee test")))
        gallery.assert_not_called()
        taps = [action[1] for action in device.actions if action[0] == "tap"]
        offset = device.pt(68, 394)
        self.assertEqual(taps[:4], [(426.0, 90.0), (100.0, 297.0), (100.0 + offset[0], 280.0 + offset[1]), (100.0, 1130.0)])

    def test_reel_without_the_plus_button_takes_the_old_route(self):
        device = FakeDevice([lines(("facebook", 100), ("What's on your mind?", 300))])
        flows = Flows(device, lambda **e: None)

        async def nothing(*_):
            return None
        with patch.object(flows, "_compose_from_gallery", nothing) as gallery, patch.object(flows, "write_caption", nothing):
            run(flows.start_post(PostItem("reel", "Clip")))
        self.assertEqual(device.actions, [])

    def test_plus_menu_without_reel_is_closed_and_the_old_route_taken(self):
        feed = lines(("facebook", 100), ("What's on your mind?", 300))
        device = FakeDevice([feed, lines(("Story", 229), ("Reels for you", 700)), feed])
        device.icons["create"] = (426.0, 90.0)
        flows = Flows(device, lambda **e: None)
        routes = []

        async def gallery():
            routes.append("gallery")

        async def nothing(*_):
            return None
        with patch.object(flows, "_compose_from_gallery", gallery), patch.object(flows, "write_caption", nothing):
            run(flows.start_post(PostItem("reel", "Clip")))
        self.assertEqual(routes, ["gallery"])
        self.assertIn(("key", phone_device.KEY_BACK), device.actions)

    def test_photo_does_not_use_the_plus_menu(self):
        device = FakeDevice([lines(("facebook", 100), ("What's on your mind?", 300))])
        device.icons["create"] = (426.0, 90.0)
        flows = Flows(device, lambda **e: None)

        async def nothing(*_):
            return None
        with patch.object(flows, "_compose_from_gallery", nothing), patch.object(flows, "write_caption", nothing):
            run(flows.start_post(PostItem("photo", "Clip")))
        self.assertEqual(device.actions, [])

    def test_caption_on_its_own_screen_is_confirmed_with_done(self):
        device = FakeDevice([lines(("What's on your mind?", 300)),
                             lines(("Add text", 90), ("Done", 90), ("Morning coffee test", 170))])
        run(Flows(device, lambda **e: None).write_caption(PostItem("photo", "Morning coffee test")))
        self.assertEqual(device.actions[-1], ("tap", (100.0, 90.0)))

    def test_create_reel_scrolls_up_to_find_the_button(self):
        device = FakeDevice([lines(("What's on your mind?", 300)), lines(("All", 200)), lines(("All", 900)),
                             lines(("Create reel", 800)), lines(("Gallery", 280)), lines(("→ Next", 1130)),
                             lines(("Describe your reel", 300))])
        run(Flows(device, lambda **e: None)._compose_from_create_reel())
        self.assertIn(("tap", (100.0, 800.0)), device.actions)
        self.assertEqual(len([a for a in device.actions if a[0] == "swipe"]), 2)

    def test_composer_opened_by_picking_is_not_skipped_with_its_next(self):
        composer = lines(("New post", 90), ("What's on your mind?", 300), ("Next", 1190))
        for screens in ([composer], [lines(("New post", 90), ("Next", 1190)), composer]):
            with self.subTest(screens=len(screens)):
                device = FakeDevice(screens)
                flows = Flows(device, lambda **e: None)

                async def gallery():
                    return (100.0, 200.0)
                with patch.object(flows, "open_gallery", gallery):
                    run(flows._compose_from_gallery())
                self.assertNotIn(("tap", (100.0, 1190.0)), device.actions)

    def test_picker_with_next_is_still_tapped(self):
        device = FakeDevice([lines(("Gallery", 200), ("Next", 60)), lines(("Gallery", 200), ("Next", 60)),
                             lines(("What's on your mind?", 300))])
        flows = Flows(device, lambda **e: None)

        async def gallery():
            return (100.0, 200.0)
        with patch.object(flows, "open_gallery", gallery):
            run(flows._compose_from_gallery())
        self.assertIn(("tap", (100.0, 60.0)), device.actions)

    def test_page_photo_picked_needs_no_next(self):
        device = FakeDevice([lines(("Say something about this photo...", 300)), lines(("Say something about this photo...", 300))])
        flows = Flows(device, lambda **e: None)

        async def gallery():
            return (100.0, 200.0)
        with patch.object(flows, "open_gallery", gallery):
            run(flows._compose_from_gallery())
        self.assertEqual(len([a for a in device.actions if a[0] == "tap"]), 1)

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
    def test_skips_pinned_post_and_finds_ours_by_its_time_label(self):
        device = FakeDevice([lines(("Pinned post", 300), ("2m", 340), ("Natalie Smith", 420), ("Just now", 450))])
        device.icons["comment"] = (60.0, 600.0)
        device.screens.insert(1, lines(("Write a comment...", 1100)))
        flows = Flows(device, lambda **e: None)
        self.assertTrue(run(flows.scan_profile(PostItem("photo", "Morning coffee test"), 3, 1)))
        self.assertIn(("tap", (60.0, 600.0)), device.actions)

    def test_caption_is_not_needed_to_find_the_post(self):
        # Job 2's profile: the caption preview cut off under the tab bar, nothing of it readable.
        shown = lines(("All", 90), ("Natalie Smith", 150), ("5m", 180), ("Sticker Mule CEO", 250),
                      ("Jaime Chung", 1009), ("29m", 1039))
        device = FakeDevice([shown, lines(("Write a comment...", 1100))])
        device.icons["comment"] = (104.0, 944.0)
        flows = Flows(device, lambda **e: None)
        self.assertTrue(run(flows.scan_profile(PostItem("reel", "Newly elected congressional candidate"), 6, 2)))
        self.assertIn(("tap", (104.0, 944.0)), device.actions)

    def test_an_older_unpinned_post_ends_the_look(self):
        device = FakeDevice([lines(("Pinned post", 300), ("3d", 340), ("Old caption", 700), ("2h", 760))])
        events = []
        flows = Flows(device, lambda **e: events.append(e))
        self.assertFalse(run(flows.scan_profile(PostItem("photo", "Morning coffee test"), 3, 1)))
        self.assertEqual(events[-1]["reason"], "reached a post 120 min old")

    def test_profile_opened_past_our_post_is_scrolled_back_up_to_find_it(self):
        past = lines(("All", 150), ("Natalie Smith", 1660), ("14m", 1730))  # only our media in view
        ours = lines(("Natalie Smith", 400), ("Just now", 450), ("Jaime Chung", 1900), ("14m", 1970))
        device = FakeDevice([past, ours, lines(("Write a comment...", 1100))])
        device.icons["comment"] = (60.0, 600.0)
        events = []
        flows = Flows(device, lambda **e: events.append(e))
        self.assertTrue(run(flows.scan_profile(PostItem("reel", "Morning coffee test"), 6, 1)))
        swipes = [a for a in device.actions if a[0] == "swipe"]
        self.assertEqual(len(swipes), 1)
        self.assertLess(swipes[0][1][1], swipes[0][2][1])  # finger moves down: the list scrolls back up
        self.assertEqual([e["event"] for e in events if e.get("step") == "find post"], ["back_up"])

    def test_an_older_post_still_ends_the_look_after_two_back_ups(self):
        device = FakeDevice([lines(("Old caption", 700), ("2h", 760))])
        events = []
        flows = Flows(device, lambda **e: events.append(e))
        self.assertFalse(run(flows.scan_profile(PostItem("photo", "Morning coffee test"), 3, 1)))
        self.assertEqual([e["event"] for e in events if e.get("step") == "find post"], ["back_up", "back_up", "stop"])
        self.assertEqual(len([a for a in device.actions if a[0] == "swipe"]), 2)

    def test_a_post_up_to_10_minutes_old_is_new_on_every_look(self):
        device = FakeDevice()
        flows = Flows(device, lambda **e: None)
        limits = []

        async def nothing(item, allowed, attempt):
            limits.append(allowed)
            return False

        async def noop():
            return None
        with patch.object(flows, "scan_profile", nothing), patch.object(flows, "open_feed", noop), \
                patch.object(flows, "open_profile", noop), patch.object(flows, "refresh_profile", noop):
            with self.assertRaises(StepFailed):
                run(flows.find_and_comment(PostItem("reel", "Clip", "hi"), published_at=-600.0))
        self.assertEqual(limits, [10, 10, 10, 10])

    def test_10m_counts_as_new_and_11m_ends_the_look(self):
        device = FakeDevice([lines(("10m", 450)), lines(("Write a comment...", 1100))])
        device.icons["comment"] = (60.0, 600.0)
        self.assertTrue(run(Flows(device, lambda **e: None).scan_profile(PostItem("reel", "x"), 10, 1)))
        device = FakeDevice([lines(("11m", 450))])
        self.assertFalse(run(Flows(device, lambda **e: None).scan_profile(PostItem("reel", "x"), 10, 1)))

    def _looks(self, kind, **kwargs):
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
                run(flows.find_and_comment(PostItem(kind, "Clip", "hi"), **kwargs))
        return [round(t) for _, t in looks]

    def test_a_photo_gets_three_looks_then_skip(self):
        self.assertEqual(self._looks("photo", published_at=0.0), [30, 55, 80])

    def test_a_reel_is_looked_for_at_30_s_then_1_2_and_2_min_later(self):
        self.assertEqual(self._looks("reel", published_at=0.0), [30, 90, 210, 330])

    def test_a_late_comment_job_looks_now_then_keeps_the_later_looks(self):
        self.assertEqual(flows_module.comment_looks("reel", 20), (30, 60, 120, 120))
        self.assertEqual(flows_module.comment_looks("reel", 100), (100, 110, 120))
        self.assertEqual(flows_module.comment_looks("photo", 500), (500,))

    def test_each_missed_look_and_the_found_post_leave_a_screenshot(self):
        device = FakeDevice([lines(("Old caption", 700), ("3h", 760))])
        flows = Flows(device, lambda **e: None)
        self.assertFalse(run(flows.scan_profile(PostItem("reel", "Morning coffee test"), 3, 2)))
        self.assertIn(("snapshot", "comment_look_2_post_not_there"), device.actions)
        device = FakeDevice([lines(("Just now", 450)), lines(("Write a comment...", 1100))])
        device.icons["comment"] = (60.0, 600.0)
        flows = Flows(device, lambda **e: None)
        self.assertTrue(run(flows.scan_profile(PostItem("reel", "Morning coffee test"), 3, 1)))
        self.assertIn(("snapshot", "comment_post_found"), device.actions)


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
        flows = Flows(device, lambda **e: events.append(e))

        async def ok():
            return None
        with patch.object(flows, "open_feed", ok), patch.object(flows, "discard_until_feed", ok):
            run(flows.get_ready())
        kinds = [e["event"] for e in events]
        self.assertIn("gallery_ready", kinds)
        self.assertIn("camera roll consent", [e.get("kind") for e in events])
        self.assertIn(("key", 4), device.actions)
        self.assertNotIn("about_to_publish", kinds)

    def test_composer_with_a_photo_video_row_opens_the_picker(self):
        device = FakeDevice([lines(("What's on your mind?", 300)), lines(("Photo/video", 700)), lines(("Gallery", 200))])
        run(Flows(device, lambda **e: None).open_gallery())
        self.assertIn(("tap", (100.0, 700.0)), device.actions)

    def test_composer_with_only_icons_opens_the_picker(self):
        device = FakeDevice([lines(("What's on your mind?", 300)),
                             lines(("New post", 90), ("What's on your mind?", 360), ("GIF", 1190), ("Next", 1190)),
                             lines(("New post", 90), ("Gallery", 200))])
        run(Flows(device, lambda **e: None).open_gallery())
        self.assertIn(("tap", (device.pt(90, 0)[0], 1190.0)), device.actions)

    def test_prompt_tap_that_opens_nothing_is_tried_again_without_a_fail_screenshot(self):
        feed = lines(("facebook", 100), ("What's on your mind?", 300))
        device = FakeDevice([feed] + [lines(("What's on your mind?", 60), ("Stories", 130))] * 12
                            + [feed, feed, lines(("Photo/video", 700)), lines(("Gallery", 200))])
        run(Flows(device, lambda **e: None).open_gallery())
        self.assertIn(("tap", (100.0, 700.0)), device.actions)
        self.assertFalse([a for a in device.actions if a[0] == "snapshot" and a[1].startswith("fail_")])

    def test_old_post_prompt_on_opening_the_composer_is_discarded(self):
        device = FakeDevice([lines(("What's on your mind?", 300)),
                             lines(("Continue editing your last post?", 500), ("CONTINUE EDITING", 640), ("DISCARD", 800), ("GIF", 1190)),
                             lines(("New post", 90), ("GIF", 1190)), lines(("New post", 90), ("Gallery", 200))])
        run(Flows(device, lambda **e: None).open_gallery())
        taps = [a[1] for a in device.actions if a[0] == "tap"]
        self.assertEqual(taps[1], (100.0, 800.0))
        self.assertEqual(taps[2], (device.pt(90, 0)[0], 1190.0))

    def test_discard_prompt_is_left_alone_outside_a_starting_step(self):
        device = FakeDevice([lines(("Discard changes?", 500), ("DISCARD", 800), ("Next", 60))])
        run(Flows(device, lambda **e: None).wait_for("composer next", ["Next"], exact=True))
        self.assertEqual(device.actions, [])

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


class WarmingTests(unittest.TestCase):
    FEED = [Line("facebook", (60, 40)), Line("What's on your mind?", (300, 200))]
    OPTIONS = {"surface": "news_feed", "auto": False, "max_seconds": 60, "pace": "quick", "reread": False,
               "long_breaks": False}

    def device(self, screens=None):
        device = FakeDevice(screens or [self.FEED])
        device.height = 1280
        device.focus = lambda: "com.facebook.katana/.LoginActivity"
        device._warmed = False
        return device

    def test_only_long_plain_text_mid_screen_can_be_read(self):
        lines = [Line("A long caption about the morning market in town", (300, 600)),
                 Line("Like", (100, 620)),
                 Line("Up in the header with plenty of words to read it", (300, 100)),
                 Line("Read the whole story at example.com right now today", (300, 640)),
                 Line("A caption with a #hashtag that opens another page", (300, 660))]
        self.assertEqual([line.text for line in screen.readable_lines(lines, 1280)],
                         ["A long caption about the morning market in town"])
        self.assertEqual(screen.readable_lines(lines + [Line("Sponsored", (100, 500))], 1280), [])

    def test_a_session_scrolls_until_its_time_is_up_and_touches_nothing_else(self):
        device = self.device()
        outcome = run(Flows(device, lambda **e: None).visit(self.OPTIONS))
        self.assertGreater(outcome["scroll_actions"], 3)
        self.assertEqual(outcome["stop_reason"], "finished")
        self.assertEqual(outcome["warming_surface"], "news_feed")
        self.assertFalse([action for action in device.actions if action[0] in ("tap", "key", "paste")])

    def test_a_post_is_opened_only_by_its_text_and_closed_only_if_it_opened(self):
        caption = Line("A long caption about the morning market in town", (300, 600))
        opened = [Line("Write a comment", (300, 1200))]
        device = self.device([self.FEED + [caption]] * 6 + [opened] + [self.FEED + [caption]])
        with patch("phone.flows.random.randint", lambda low, high: low):
            run(Flows(device, lambda **e: None).browse(60))
        taps = [action[1] for action in device.actions if action[0] == "tap"]
        self.assertIn(caption.point, taps)
        self.assertEqual(set(taps), {caption.point})

    def warm_taps(self, screens):
        device = self.device(screens)
        with patch("phone.flows.random.random", lambda: 0.1), patch("phone.flows.random.randint", lambda low, high: 1):
            run(Flows(device, lambda **e: None).warm(scrolls=6))
        return device, [action[1] for action in device.actions if action[0] == "tap"]

    def test_the_warm_up_before_a_post_only_taps_post_text(self):
        caption = Line("A long caption about the morning market in town", (300, 600))
        _, taps = self.warm_taps([self.FEED + [caption]])
        self.assertTrue(taps)
        self.assertEqual(set(taps), {caption.point})

    def test_the_warm_up_before_a_post_just_scrolls_without_post_text(self):
        device, taps = self.warm_taps([self.FEED + [Line("Like", (100, 620))]])
        self.assertEqual(taps, [])
        self.assertTrue([action for action in device.actions if action[0] == "swipe"])

    def test_back_is_not_pressed_when_the_caption_only_expanded(self):
        caption = Line("A long caption about the morning market in town", (300, 600))
        device = self.device([self.FEED + [caption]])
        run(Flows(device, lambda **e: None)._read_post(caption, lambda seconds: asyncio.sleep(0)))
        self.assertNotIn(("key", phone_device.KEY_BACK), device.actions)

    def test_back_closes_the_full_screen_viewer_a_post_opened(self):
        caption = Line("A long caption about the morning market in town", (300, 600))
        device = self.device([self.FEED])
        windows = iter(["com.facebook.katana/.LoginActivity", "com.facebook.katana/.ImmersiveActivity"])
        device.focus = lambda: next(windows, "com.facebook.katana/.LoginActivity")
        run(Flows(device, lambda **e: None)._read_post(caption, lambda seconds: asyncio.sleep(0)))
        self.assertIn(("key", phone_device.KEY_BACK), device.actions)

    def test_a_request_to_stop_ends_the_session_at_the_next_safe_point(self):
        device = self.device()
        asks = iter([False, False, True])
        outcome = run(Flows(device, lambda **e: None).visit(self.OPTIONS, lambda: next(asks, True)))
        self.assertLessEqual(outcome["scroll_actions"], 1)
        self.assertEqual(outcome["stop_reason"], "asked_to_stop")

    def test_a_logged_out_account_is_blocked_not_failed(self):
        device = self.device([[Line("Log in", (300, 600)), Line("Create new account", (300, 800))]])

        async def no_feed(self_):
            return None
        with patch.object(Flows, "open_feed", no_feed):
            result = run(job.run_warming(device, self.OPTIONS, lambda **e: None))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["account_blocked"])
        self.assertEqual(device.actions[-1], ("key", job.KEY_HOME))

    def test_leaving_facebook_reopens_it_once_then_fails(self):
        device = self.device()
        device.focus = lambda: "com.android.launcher/.Launcher"
        opens = []

        async def no_feed(self_):
            opens.append(1)
        with patch.object(Flows, "open_feed", no_feed):
            result = run(job.run_warming(device, self.OPTIONS, lambda **e: None))
        self.assertEqual(result["status"], "failed")
        self.assertIn("left Facebook", result["reason"])
        self.assertEqual(len(opens), 2)  # the job's open, then the one reopening

    def test_a_finished_session_reports_completed_and_goes_home(self):
        device = self.device()

        async def no_feed(self_):
            return None
        with patch.object(Flows, "open_feed", no_feed):
            result = run(job.run_warming(device, self.OPTIONS, lambda **e: None))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(device.actions[-1], ("key", job.KEY_HOME))


class JobTests(unittest.TestCase):
    def run_job(self, publish=None, wait=None, start=None, comment=None, comment_now=True):
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
                   patch.object(Flows, "warm", ok),
                   patch.object(Flows, "start_post", start or ok), patch.object(Flows, "publish", publish or tapped),
                   patch.object(Flows, "wait_published", wait or published),
                   patch.object(Flows, "find_and_comment", comment or (lambda *a, **k: ok()))]
        for item in patches:
            item.start()
        try:
            with patch.object(job, "media_mime", lambda path: "image/jpeg"):
                result = run(job.run_post(device, PostItem("photo", "Morning coffee test", "First!"),
                                          "/media/a.jpg", lambda **e: events.append(e), comment_now=comment_now))
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

    def test_confirmed_post_lingers_and_scrolls_before_home_with_comment_deferred(self):
        with patch.object(job.random, "uniform", return_value=4.0):
            result, device, events = self.run_job(comment_now=False)
        self.assertEqual((result["status"], result["comment"]), ("published", "pending"))
        self.assertEqual(result["seconds"], 9)  # two four-second pauses plus the swipe
        self.assertEqual([a[0] for a in device.actions], ["swipe", "key"])
        self.assertEqual(device.actions[-1], ("key", phone_device.KEY_HOME))
        self.assertIn("after_success", [e["event"] for e in events])

    def test_confirmed_follow_up_comment_lingers_before_home(self):
        device = FakeDevice([lines(("facebook", 100), ("What's on your mind?", 200))])
        events = []

        async def commented(*args):
            return 1

        with patch.object(Flows, "find_and_comment", commented), patch.object(job.random, "uniform", return_value=4.0):
            result = run(job.run_comment(device, PostItem("reel", "Clip", "First!"), lambda **e: events.append(e)))
        self.assertEqual(result["comment"], "posted")
        self.assertGreaterEqual(device.now(), 9)
        self.assertEqual([a[0] for a in device.actions], ["swipe", "key"])
        self.assertEqual(device.actions[-1], ("key", phone_device.KEY_HOME))

    def test_optional_browsing_failure_keeps_publication_confirmed(self):
        async def dropped(*args):
            raise ConnectionResetError("stream closed")

        with patch.object(Flows, "scroll", dropped):
            result, device, events = self.run_job(comment_now=False)
        self.assertEqual(result["status"], "published")
        self.assertEqual(device.deleted, ["content://agent/media/1"])
        self.assertEqual(device.actions[-1], ("key", phone_device.KEY_HOME))
        self.assertIn("after_success_skipped", [e["event"] for e in events])

    def test_uncertain_comment_does_not_browse(self):
        async def uncertain(*args):
            raise job.CommentUncertain("send not confirmed")

        result, device, events = self.run_job(comment=uncertain)
        self.assertEqual((result["status"], result["comment"]), ("published", "uncertain"))
        self.assertNotIn("after_success", [e["event"] for e in events])
        self.assertFalse(any(a[0] == "swipe" for a in device.actions))

    def test_failure_before_the_tap_is_a_plain_failure(self):
        async def broken(self_, item):
            raise StepFailed("composer: none of ['Gallery'] appeared")
        result, device, _ = self.run_job(start=broken)
        self.assertEqual((result["status"], result["stage"]), ("failed", "compose"))
        self.assertEqual(device.deleted, ["content://agent/media/1"])

    def test_lost_stream_after_the_tap_is_uncertain_and_keeps_the_media(self):
        async def dropped(self_, item, at):
            raise ConnectionResetError("stream closed")
        result, device, events = self.run_job(wait=dropped)
        self.assertEqual((result["status"], result["stage"]), ("uncertain", "confirm"))
        self.assertEqual(device.deleted, [])
        self.assertEqual(result["media_left"], "content://agent/media/1")
        self.assertNotIn("after_success", [e["event"] for e in events])

    def test_approval_screen_stops_the_job_without_breaking_cleanup(self):
        async def stayed(self_, item):
            raise phone_device.ApprovalScreenUp("stayed open")

        async def stuck(self_):
            raise phone_device.ApprovalScreenUp("stayed open")
        with patch.object(job.Flows, "discard_until_feed", stuck):
            with patch.object(Flows, "open_feed", lambda self_: asyncio.sleep(0)), \
                    patch.object(Flows, "warm", lambda self_, **kw: asyncio.sleep(0)), \
                    patch.object(Flows, "start_post", stayed), \
                    patch.object(job, "media_mime", lambda path: "image/jpeg"):
                result = run(job.run_post(FakeDevice(), PostItem("photo", "Morning coffee test"), "/media/a.jpg",
                                          lambda **e: None))
        self.assertEqual((result["status"], result["stage"]), ("failed", "compose"))
        self.assertIn("stayed open", result["reason"])

    def test_logged_out_before_the_tap_blocks_the_account(self):
        async def logged_out(self_, item):
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
