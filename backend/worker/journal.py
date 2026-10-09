"""SQLite acceptance, command deduplication, and durable at-least-once outbox."""
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path

from .protocol import envelope


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, token TEXT NOT NULL, job TEXT NOT NULL,
                local_id TEXT NOT NULL UNIQUE, profile_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'accepted',
                fingerprint TEXT, sequence INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS outbox (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, content TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
        else:
            with self.db:
                yield

    def close(self):
        self.db.close()

    def get(self, attempt_id):
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        return dict(row) if row else None

    def attempts(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM attempts ORDER BY rowid")]

    def put_message(self, message):
        self.db.execute("INSERT OR IGNORE INTO outbox(id,message) VALUES(?,?)",
                        (message["message_id"], json.dumps(message)))

    def queue(self, message):
        with self.transaction():
            self.put_message(message)
        return message

    def accept(self, message, job, local_id, profile_id=""):
        attempt_id, token = message.attempt()
        previous = self.get(attempt_id)
        serialized = json.dumps(job, sort_keys=True)
        if previous and (previous["token"] != token or previous["job"] != serialized):
            raise ValueError("Attempt identity was reused with different content or fencing token")
        reply = envelope("job_accept", {"local_execution_id": local_id},
                         attempt_id=attempt_id, lease_token=token, reply_to=message.message_id)
        with self.transaction():
            if not previous:
                self.db.execute("INSERT INTO attempts(id,token,job,local_id,profile_id) VALUES(?,?,?,?,?)",
                                (attempt_id, token, serialized, local_id, profile_id))
            self.put_message(reply)
        return reply

    def state(self, attempt_id, state):
        with self.transaction():
            self.db.execute("UPDATE attempts SET state=? WHERE id=?", (state, attempt_id))

    def observation(self, attempt_id, payload, terminal=False):
        row = self.get(attempt_id)
        fingerprint = json.dumps(payload, sort_keys=True)
        if row["fingerprint"] == fingerprint or row["state"] == "terminal":
            return None
        sequence = row["sequence"] + 1
        result = envelope("execution_result" if terminal else "execution_event",
                          {**payload, "sequence": sequence}, attempt_id=attempt_id,
                          lease_token=row["token"])
        with self.transaction():
            self.db.execute("UPDATE attempts SET fingerprint=?,sequence=?,state=? WHERE id=?",
                            (fingerprint, sequence, "terminal" if terminal else row["state"], attempt_id))
            self.put_message(result)
        return result

    def pending(self, limit=100):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT message FROM outbox ORDER BY ordinal LIMIT ?", (limit,))]

    def acknowledge(self, reply_to, expected_type, attempt_id=None, token=None):
        row = self.db.execute("SELECT message FROM outbox WHERE id=?", (reply_to,)).fetchone()
        if not row:
            return False
        message = json.loads(row[0])
        if message["type"] != expected_type:
            raise ValueError("Acknowledgement type mismatch")
        if message.get("attempt_id") != attempt_id or message.get("lease_token") != token:
            raise ValueError("Acknowledgement attempt or token mismatch")
        with self.transaction():
            self.db.execute("DELETE FROM outbox WHERE id=?", (reply_to,))
        return True

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))

    def command(self, message, apply):
        content = json.dumps(message.model_dump(), sort_keys=True)
        row = self.db.execute("SELECT content,result FROM commands WHERE id=?", (message.message_id,)).fetchone()
        if row and row[0] != content:
            raise ValueError("Command ID reused with different content")
        with self.transaction():
            if not self.db.in_transaction:
                self.db.execute("BEGIN IMMEDIATE")
            if row:
                result = json.loads(row[1])
            else:
                result = envelope('command_result', apply(), reply_to=message.message_id,
                                  **({'attempt_id': message.attempt_id, 'lease_token': message.lease_token}
                                     if message.attempt_id else {}))
                self.db.execute("INSERT INTO commands VALUES(?,?,?)", (message.message_id, content, json.dumps(result)))
            self.put_message(result)
        return result
