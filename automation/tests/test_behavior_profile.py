import json
from pathlib import Path
import tempfile
import unittest

from engine.behavior_profile import BehaviorSession, load_behavior_session


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


if __name__ == "__main__":
    unittest.main()
