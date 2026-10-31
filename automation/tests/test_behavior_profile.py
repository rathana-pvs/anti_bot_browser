import json
from pathlib import Path
import tempfile
import unittest

from engine.behavior_profile import BehaviorSession, Persona, load_behavior_session
from engine.text_entry_policy import choose_entry_plan


class BehaviorSessionTests(unittest.TestCase):
    def test_same_profile_and_run_are_reproducible(self):
        first = BehaviorSession("medium", 12345, "run-1")
        second = BehaviorSession("medium", 12345, "run-1")

        self.assertEqual(first.seed_hash, second.seed_hash)
        self.assertEqual(
            [first.stream("mouse").random() for _ in range(4)],
            [second.stream("mouse").random() for _ in range(4)],
        )

    def test_streams_do_not_affect_each_other(self):
        first = BehaviorSession("medium", 12345, "run-2")
        second = BehaviorSession("medium", 12345, "run-2")
        first.stream("mouse").random()
        first.stream("mouse").random()

        self.assertEqual(
            first.stream("typing").random(),
            second.stream("typing").random(),
        )

    def test_profile_configuration_selects_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_dir = root / "profile_001"
            profile_dir.mkdir()
            (profile_dir / "config.json").write_text(
                json.dumps({"behavior_mode": "slow", "behavior_seed": 456}),
                encoding="utf-8",
            )

            session = load_behavior_session(
                "profile_001", "run-3", profiles_root=root
            )

        self.assertEqual(session.mode, "slow")
        self.assertEqual(session.preset.typing_wpm, (32, 48))

    def test_invalid_configuration_falls_back_to_medium(self):
        with tempfile.TemporaryDirectory() as directory:
            session = load_behavior_session(
                "missing", "run-4", profiles_root=Path(directory)
            )
        self.assertEqual(session.mode, "medium")


    def test_persona_is_stable_for_an_account_and_differs_between_accounts(self):
        first = BehaviorSession("medium", 12345, "run-a")
        second = BehaviorSession("slow", 12345, "run-b")
        self.assertEqual(first.persona, second.persona)
        self.assertNotEqual(Persona.from_seed(12345), Persona.from_seed(54321))

    def test_persona_stays_within_its_limits(self):
        for seed in range(1, 300):
            persona = Persona.from_seed(seed)
            self.assertLessEqual(persona.preparation_scale, 1.0)
            self.assertLessEqual(persona.linger_seconds[1], 60.0)
            self.assertLess(persona.linger_seconds[0], persona.linger_seconds[1])
            self.assertLess(persona.scroll_notches[0], persona.scroll_notches[1])
            self.assertTrue(6 <= persona.active_hours[0] <= 11)

    def test_natural_session_does_not_change_existing_streams(self):
        plain = BehaviorSession("medium", 12345, "run-c")
        natural = BehaviorSession("medium", 12345, "run-c", natural=True)
        self.assertEqual((plain.pace, plain.wpm), (natural.pace, natural.wpm))
        self.assertEqual(plain.stream("mouse").random(), natural.stream("mouse").random())
        self.assertNotIn("persona", plain.summary())
        self.assertIn("persona", natural.summary())

    def test_every_account_runs_its_natural_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profile_001").mkdir()
            (root / "profile_001" / "config.json").write_text(
                json.dumps({"behavior_seed": 9, "automation": {"natural_session": True}}), encoding="utf-8")
            (root / "profile_002").mkdir()
            (root / "profile_002" / "config.json").write_text(
                json.dumps({"behavior_seed": 9, "automation": {"natural_session": False}}), encoding="utf-8")
            self.assertTrue(load_behavior_session("profile_001", "r", profiles_root=root).natural)
            self.assertTrue(load_behavior_session("profile_002", "r", profiles_root=root).natural)
            self.assertTrue(load_behavior_session("profile_missing", "r", profiles_root=root).natural)

    def test_paste_preference_applies_only_with_the_switch_on(self):
        plain = [choose_entry_plan("short caption", "caption", BehaviorSession("medium", seed, "r")).method
                 for seed in range(1, 200)]
        expected = ["type" if BehaviorSession("medium", seed, "r").stream("entry_strategy").random() < 0.5 else "paste"
                    for seed in range(1, 200)]
        self.assertEqual(plain, expected)
        heavy_paster = next(seed for seed in range(1, 2000) if Persona.from_seed(seed).paste_preference > 0.75)
        session = BehaviorSession("medium", heavy_paster, "r", natural=True)
        methods = [choose_entry_plan("short caption", "caption", session).method for _ in range(200)]
        self.assertGreater(methods.count("paste"), 120)


if __name__ == "__main__":
    unittest.main()
