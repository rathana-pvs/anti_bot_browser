import asyncio
import json
import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.observability import JsonFormatter, RequestLoggingMiddleware


def test_json_formatter_includes_correlation_fields():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "done", (), None)
    record.request_id = "req-1"
    record.execution_id = "exec-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "done"
    assert payload["request_id"] == "req-1"
    assert payload["execution_id"] == "exec-1"


def test_request_id_is_returned_and_preserved():
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.add_middleware(RequestLoggingMiddleware)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/health", headers={"X-Request-ID": "client-request"})

    response = asyncio.run(run())
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "client-request"


def test_unsafe_request_id_is_replaced():
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.add_middleware(RequestLoggingMiddleware)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/health", headers={"X-Request-ID": "unsafe value"})

    response = asyncio.run(run())
    assert response.headers["X-Request-ID"] != "unsafe value"
    assert len(response.headers["X-Request-ID"]) == 32
