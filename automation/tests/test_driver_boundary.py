"""Only the container driver may run xdotool or raw container commands.

Workflows send input through HumanInput, which calls named driver primitives.
A raw call elsewhere bypasses pointer paths and typing cadence, and would not
work on another surface such as an Android phone.
"""

import io
import tokenize
import unittest
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parents[1]
DRIVER = AUTOMATION / "engine" / "container_client.py"
RAW_CALLS = {"exec_cmd", "xdo", "batch_xdo"}


def violations(path: Path) -> list[str]:
    found = []
    tokens = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
    for previous, token in zip(tokens, tokens[1:]):
        if token.type == tokenize.STRING and "xdotool" in token.string:
            found.append(f"{path.relative_to(AUTOMATION)}:{token.start[0]} xdotool string")
        if token.type == tokenize.NAME and token.string in RAW_CALLS and previous.string == ".":
            found.append(f"{path.relative_to(AUTOMATION)}:{token.start[0]} .{token.string}")
    return found


class DriverBoundaryTests(unittest.TestCase):
    def test_raw_input_stays_inside_the_driver(self):
        files = [AUTOMATION / name for name in ("engine", "modules", "tasks")]
        sources = [path for folder in files for path in folder.rglob("*.py")]
        sources += list(AUTOMATION.glob("*.py"))
        found = [item for path in sources if path != DRIVER for item in violations(path)]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
