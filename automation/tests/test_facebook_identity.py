"""The login check tells a Page from the personal profile behind it."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skills.session import facebook_identity


def items(*texts):
    return [{"text": text} for text in texts]


class FacebookIdentityTests(unittest.TestCase):
    def test_page_management_marks_a_page(self):
        self.assertEqual(facebook_identity(items("Manage Page", "Dyonte Crawford", "2.9K followers • 0 following")), "page")
        self.assertEqual(facebook_identity(items("Creator Dashboard", "Edit", "Advertise")), "page")

    def test_friend_count_or_story_marks_the_personal_profile(self):
        self.assertEqual(facebook_identity(items("Sor R", "1 friend", "Add to story", "Edit profile")), "personal")
        self.assertEqual(facebook_identity(items("Memories", "Saved", "Groups")), "personal")

    def test_unclear_screens_stay_unknown(self):
        self.assertIsNone(facebook_identity(items("Facebook", "Search Facebook", "Reels")))
        self.assertIsNone(facebook_identity([]))


if __name__ == "__main__":
    unittest.main()
