import pytest


def test_trying_a_proxy_waits_for_the_vpn_of_a_user_just_started(monkeypatch):
    from backend.services import android_ownership
    answers = iter([RuntimeError("no answer"), {"service_running": False}, {"service_running": True}])
    calls = []

    def fake_call(client, serial, user_id, method, timeout, arg=None):
        calls.append(method)
        if method == "status":
            answer = next(answers)
            if isinstance(answer, Exception):
                raise android_ownership.AdbError(str(answer))
            return answer
        return {"applied": True}

    monkeypatch.setattr(android_ownership, "agent_call", fake_call)
    monkeypatch.setattr(android_ownership.time, "sleep", lambda seconds: None)
    entry = {"host": "10.0.0.1", "port": 1080, "username": "u", "password": "p"}
    assert android_ownership._try_proxy(None, "PIXEL", 12, entry) == {"applied": True}
    assert calls == ["status", "status", "status", "try_config"]


def test_a_vpn_that_never_starts_gives_a_clear_reason(monkeypatch):
    from backend.services import android_ownership
    monkeypatch.setattr(android_ownership, "agent_call", lambda *a, **k: {"service_running": False})
    now = [0.0]
    with pytest.raises(android_ownership.AdbError, match="VPN didn't start"):
        android_ownership._wait_for_vpn(None, "PIXEL", 12, timeout=3, sleep=lambda s: now.__setitem__(0, now[0] + s),
                                        clock=lambda: now[0])
