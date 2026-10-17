import asyncio

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from backend.idempotency import IdempotencyMiddleware, IdempotencyStore


def call(app, body, key="same-key"):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/api/queue/batch", json=body, headers={"Idempotency-Key": key},
            )
    return asyncio.run(run())


def test_completed_request_is_replayed_without_running_handler(tmp_path):
    app = FastAPI()
    calls = []

    @app.post("/api/queue/batch")
    async def create(request: Request):
        calls.append(await request.json())
        return {"created": len(calls)}

    app.add_middleware(IdempotencyMiddleware, store=IdempotencyStore(tmp_path / "keys.sqlite3"))
    first = call(app, {"name": "one"})
    replay = call(app, {"name": "one"})
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == {"created": 1}
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(calls) == 1


def test_key_cannot_be_reused_for_different_request(tmp_path):
    app = FastAPI()

    @app.post("/api/queue/batch")
    async def create():
        return {"created": True}

    app.add_middleware(IdempotencyMiddleware, store=IdempotencyStore(tmp_path / "keys.sqlite3"))
    assert call(app, {"name": "one"}).status_code == 200
    conflict = call(app, {"name": "two"})
    assert conflict.status_code == 409
    assert "different request" in conflict.json()["detail"]


def test_request_without_key_remains_backward_compatible(tmp_path):
    app = FastAPI()
    calls = []

    @app.post("/api/queue/batch")
    async def create():
        calls.append(True)
        return {"created": len(calls)}

    app.add_middleware(IdempotencyMiddleware, store=IdempotencyStore(tmp_path / "keys.sqlite3"))

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/queue/batch", json={})
            await client.post("/api/queue/batch", json={})
    asyncio.run(run())
    assert len(calls) == 2

