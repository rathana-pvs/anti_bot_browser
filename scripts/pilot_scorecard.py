#!/usr/bin/env python3
"""Print the local reliability scorecard without starting or retrying work."""

import json
import sys

from backend.services.queue_service import load_posting_queue
from backend.services.reliability_service import build_reliability_scorecard, flatten_queue_executions


def main() -> int:
    result = build_reliability_scorecard(flatten_queue_executions(load_posting_queue()))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 2 if result["status"] == "insufficient_data" else 1


if __name__ == "__main__":
    raise SystemExit(main())

