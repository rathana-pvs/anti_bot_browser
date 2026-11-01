"""Calls to the companion app's control provider (`content call --user N`).

Standard library and `adb_client` only, so the phone automation runner can import it without the rest
of the backend.
"""

from __future__ import annotations

import base64
import json
import re

from backend.services.adb_client import AdbClient, AdbError

AGENT_PACKAGE = "com.isolatedbrowser.agent"
AGENT_AUTHORITY = f"{AGENT_PACKAGE}.control"
_REPLY = re.compile(r"json=(\{.*\})\}\]\s*$", re.S)


def parse_agent_reply(output: str) -> dict:
    match = _REPLY.search(output.strip())
    if not match:
        raise AdbError("The companion app did not answer. It may not be installed in this Android user.")
    return json.loads(match.group(1))


class AgentRefused(AdbError):
    """The companion app answered but refused; `code` is its error, e.g. "wrong_pin"."""

    def __init__(self, method: str, code: str):
        super().__init__(f"The companion app refused {method}: {code}")
        self.code = code


def agent_call(client: AdbClient, serial: str, user_id: int, method: str, timeout: float,
               arg: dict | None = None) -> dict:
    """`arg` goes base64-encoded, so neither the shell nor the command line needs quoting."""
    extra = ["--arg", base64.b64encode(json.dumps(arg).encode()).decode()] if arg is not None else []
    output = client.shell(serial, "content", "call", "--user", str(int(user_id)),
                          "--uri", f"content://{AGENT_AUTHORITY}", "--method", method, *extra, timeout=timeout)
    reply = parse_agent_reply(output)
    if not reply.get("ok"):
        raise AgentRefused(method, reply.get("error") or "unknown error")
    return reply
