import unittest

from engine.text_matcher import OcrTextMatcher


class OcrTextMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = OcrTextMatcher(default_threshold=0.78)

    def evaluate(self, received):
        return self.matcher.evaluate(
            expected="Describe your reel…",
            received=received,
            required_tokens=("your", "reel"),
        )

    def test_returns_structured_score_and_threshold(self):
        result = self.evaluate("pescribe your reel…")

        self.assertTrue(result.matched)
        self.assertGreaterEqual(result.score, result.threshold)
        self.assertEqual(result.threshold, 0.78)
        self.assertEqual(
            set(result.components),
            {"characters", "word_order", "start", "end", "length"},
        )

    def test_accepts_representative_ocr_variations(self):
        for received in (
            "pescribe your reel ..",
            "cescribe your reel...",
            "descnbe your reel",
            "Describe your ree1",
        ):
            self.assertTrue(self.evaluate(received).matched, received)

    def test_required_tokens_block_similar_but_incomplete_text(self):
        result = self.evaluate("Describe reel")

        self.assertFalse(result.matched)
        self.assertFalse(result.required_tokens_present)

    def test_required_context_tokens_allow_single_ocr_substitution(self):
        for received in ("Describe y0ur reel", "Describe your ree1"):
            result = self.evaluate(received)
            self.assertTrue(result.required_tokens_present, received)
            self.assertTrue(result.matched, received)

    def test_rejects_unrelated_reel_status(self):
        self.assertFalse(self.evaluate("your reel is safe to publish").matched)

    def test_accepts_remixing_setting_when_ocr_splits_trailing_audio(self):
        result = self.matcher.evaluate(
            expected="Remixing and use of original audio",
            received="Remixing and use of original",
            threshold=0.75,
            required_tokens=("remixing", "original"),
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.score, 0.816946)
        self.assertEqual(result.threshold, 0.75)

    def test_supporting_remixing_phrase_accepts_ocr_use_substitution(self):
        result = self.matcher.evaluate(
            expected="Remixing and use of original audio",
            received="remixing and vse of original",
            threshold=0.75,
            required_tokens=("remixing", "original"),
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.score, 0.766066)

    def test_short_action_labels_require_exact_normalized_match(self):
        matcher = OcrTextMatcher(default_threshold=0.70)

        self.assertTrue(matcher.evaluate(expected="Post", received="POST").matched)
        self.assertFalse(matcher.evaluate(expected="Post", received="Boost").matched)


if __name__ == "__main__":
    unittest.main()
