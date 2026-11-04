import asyncio
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from backend.idempotency import IdempotencyMiddleware, IdempotencyStore


def scope():
    return {'type': 'http', 'method': 'POST', 'path': '/api/queue/batch',
            'query_string': b'', 'headers': [(b'idempotency-key', b'disconnect-test')]}


def test_disconnect_during_body_does_not_spin_or_claim_work(tmp_path):
    async def run():
        store = IdempotencyStore(tmp_path / 'keys.sqlite3')
        store.claim = Mock(side_effect=AssertionError('Incomplete body must not claim work'))
        app, send = AsyncMock(), AsyncMock()
        # A bounded fake makes the old infinite-loop defect fail immediately.
        receive = AsyncMock(side_effect=[
            {'type': 'http.request', 'body': b'{', 'more_body': True},
            {'type': 'http.disconnect'},
            AssertionError('Read after disconnect'),
        ])
        await IdempotencyMiddleware(app, store)(scope(), receive, send)
        assert receive.await_count == 2
        app.assert_not_awaited()
        send.assert_not_awaited()
    asyncio.run(run())


def test_buffered_body_is_replayed_once_then_real_disconnect_is_forwarded(tmp_path):
    async def run():
        received = []
        async def app(scope, receive, send):
            received.append(await receive())
            received.append(await receive())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})
        receive = AsyncMock(side_effect=[
            {'type': 'http.request', 'body': b'{}', 'more_body': False},
            {'type': 'http.disconnect'},
        ])
        await IdempotencyMiddleware(app, IdempotencyStore(tmp_path / 'keys.sqlite3'))(
            scope(), receive, AsyncMock())
        assert received == [{'type': 'http.request', 'body': b'{}', 'more_body': False},
                            {'type': 'http.disconnect'}]
        assert receive.await_count == 2
    asyncio.run(run())


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


def test_protected_request_with_desktop_auth_keeps_api_responsive(tmp_path, monkeypatch):
    from backend.security import DesktopSessionAuthMiddleware
    monkeypatch.setenv('MANAGER_API_TOKEN', 'test-desktop-token')
    app = FastAPI()
    calls = []

    @app.post('/api/queue/batch')
    async def create(request: Request):
        calls.append(await request.json())
        await asyncio.sleep(.01)
        return {'created': len(calls)}

    @app.get('/health')
    async def health():
        return {'ok': True}

    app.add_middleware(DesktopSessionAuthMiddleware)
    app.add_middleware(IdempotencyMiddleware, store=IdempotencyStore(tmp_path / 'keys.sqlite3'))

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test',
                               headers={'X-Manager-Token': 'test-desktop-token'}) as client:
            created, health = await asyncio.gather(
                client.post('/api/queue/batch', json={'name': 'one'}, headers={'Idempotency-Key': 'auth-test'}),
                client.get('/health'))
            assert created.status_code == health.status_code == 200
            replay = await client.post('/api/queue/batch', json={'name': 'one'},
                                       headers={'Idempotency-Key': 'auth-test'})
            assert replay.headers['Idempotency-Replayed'] == 'true'
            assert len(calls) == 1
    asyncio.run(run())

