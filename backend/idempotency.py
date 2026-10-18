"""Durable idempotency protection for operations that create local work."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import DATA_DIR


PROTECTED_OPERATIONS = {
    ("POST", "/api/automation/run"),
    ("POST", "/api/queue/batch"),
}


def is_protected_operation(method: str, path: str) -> bool:
    return (method, path) in PROTECTED_OPERATIONS or (
        method == "POST" and path.startswith("/api/profiles/") and path.endswith("/live/start")
    )


class IdempotencyStore:
    def __init__(self, path: Path, retention_seconds: int = 24 * 60 * 60, pending_seconds: int = 10 * 60):
        self.path = path
        self.retention_seconds = retention_seconds
        self.pending_seconds = pending_seconds
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS api_idempotency (
                key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                status_code INTEGER,
                content_type TEXT,
                body BLOB,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        return db

    def claim(self, key: str, fingerprint: str) -> tuple[str, dict | None]:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM api_idempotency WHERE updated_at < ?", (now - self.retention_seconds,))
            row = db.execute(
                "SELECT fingerprint,state,status_code,content_type,body,updated_at FROM api_idempotency WHERE key=?",
                (key,),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    return "conflict", None
                if row[1] == "complete":
                    return "replay", {"status_code": row[2], "content_type": row[3], "body": row[4] or b""}
                if now - float(row[5]) <= self.pending_seconds:
                    return "pending", None
                db.execute(
                    "UPDATE api_idempotency SET created_at=?,updated_at=? WHERE key=?",
                    (now, now, key),
                )
                return "new", None
            db.execute(
                "INSERT INTO api_idempotency(key,fingerprint,state,created_at,updated_at) VALUES(?,?,\'pending\',?,?)",
                (key, fingerprint, now, now),
            )
            return "new", None

    def complete(self, key: str, fingerprint: str, status_code: int, content_type: str, body: bytes) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE api_idempotency
                   SET state='complete',status_code=?,content_type=?,body=?,updated_at=?
                   WHERE key=? AND fingerprint=?""",
                (status_code, content_type, body, time.time(), key, fingerprint),
            )

    def abandon(self, key: str, fingerprint: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM api_idempotency WHERE key=? AND fingerprint=? AND state='pending'",
                (key, fingerprint),
            )


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, store: IdempotencyStore | None = None):
        self.app = app
        self.store = store or IdempotencyStore(DATA_DIR / "api-idempotency.sqlite3")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_protected_operation(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_key = headers.get(b"idempotency-key")
        if not raw_key:
            await self.app(scope, receive, send)
            return
        key = raw_key.decode("utf-8", "ignore").strip()
        if not key or len(key) > 128:
            await JSONResponse({"detail": "Idempotency-Key must contain 1 to 128 characters"}, 400)(scope, receive, send)
            return

        body_parts: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body_parts.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(body_parts)
        fingerprint = hashlib.sha256(
            b"\0".join((scope["method"].encode(), scope["path"].encode(), scope.get("query_string", b""), body))
        ).hexdigest()
        outcome, saved = self.store.claim(key, fingerprint)
        if outcome == "conflict":
            await JSONResponse({"detail": "Idempotency-Key was already used for a different request"}, 409)(scope, receive, send)
            return
        if outcome == "pending":
            await JSONResponse({"detail": "An identical request with this Idempotency-Key is still running"}, 409)(scope, receive, send)
            return
        if outcome == "replay" and saved:
            await Response(
                content=saved["body"], status_code=saved["status_code"],
                media_type=saved["content_type"], headers={"Idempotency-Replayed": "true"},
            )(scope, receive, send)
            return

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        start_message: Message | None = None
        response_body: list[bytes] = []

        async def capture_send(message: Message) -> None:
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = message
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
            if start_message is not None and int(start_message["status"]) < 500:
                response_headers = {key.lower(): value for key, value in start_message.get("headers", [])}
                content_type = response_headers.get(b"content-type", b"application/json").decode("ascii", "ignore")
                self.store.complete(key, fingerprint, int(start_message["status"]), content_type, b"".join(response_body))
            else:
                self.store.abandon(key, fingerprint)
        except BaseException:
            self.store.abandon(key, fingerprint)
            raise

