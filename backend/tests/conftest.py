"""Shared isolation guards for backend tests.

The application lifespan intentionally leaves the process in a shutting-down
state.  TestClient lifespans, however, run inside the pytest process and must
not leak that state into the next test.
"""

import pytest

from backend.services import container_lifecycle


@pytest.fixture(autouse=True)
def reset_process_lifecycle_state(monkeypatch):
    """Give every test the same process-start lifecycle state."""
    monkeypatch.setattr(container_lifecycle, "SHUTTING_DOWN", False)

