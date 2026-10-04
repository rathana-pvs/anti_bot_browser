"""The launch endpoint must return the task state expected by the manager UI."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routers import automation


@pytest.mark.parametrize("task", ["warming", "post", "reel", "comment"])
def test_launch_returns_running_state(monkeypatch, task):
    state = {"profile_id": "profile-test", "task": task, "status": "running", "logs": []}
    launch = AsyncMock(return_value=state)
    monkeypatch.setattr(automation, "launch_manual_automation", launch)
    app = FastAPI()
    app.include_router(automation.router)
    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/automation/run", json={
                "profile_id": "profile-test", "task": task, "scrolls": 4,
            })
    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["state"] == state
    assert launch.await_args.kwargs["scrolls"] == 4


def test_launch_capacity_error_remains_an_error(monkeypatch):
    result = {"status_code": 409, "error": "Scheduler capacity unavailable"}
    monkeypatch.setattr(automation, "launch_manual_automation", AsyncMock(return_value=result))
    app = FastAPI()
    app.include_router(automation.router)
    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/automation/run", json={
                "profile_id": "profile-test", "task": "warming",
            })
    response = asyncio.run(request())
    assert response.status_code == 409
    assert response.json()["detail"] == result
