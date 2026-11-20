"""Replace and move: Webshare gives a proxy a new IP, and its accounts move to the new proxy.

The old IP stops working the moment Webshare replaces it, so nothing is replaced until every account
on the proxy is ready and inactive:

1. No job may run on an account or on its phone; a running job is never stopped. One that isn't
   ready leaves the proxy as it was, with the reason.
2. A PC account's browser is stopped. A phone account's Android user is taken off the screen (the
   phone goes to its main user) and stopped, and the phone must be connected and managed by this PC,
   since the new proxy is tried inside that user before the phone keeps it.
3. Webshare replaces the IP; the new proxy joins the pool (webshare_service.replace_one).
4. Each account moves through the same path as Change proxy (proxy_change.change), checked again
   for a job just before. An account that can't move stays on the old proxy, reported, and the old
   proxy is kept for it; once every account has moved, the old proxy is deleted.

Nothing is started again afterwards: browsers stay closed and phones stay on their main user. A
proxy Webshare already replaced whose accounts are still on it only gets step 4.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from backend.services import proxy_change, proxy_service, webshare_service


class NotReady(Exception):
    pass


def _name(profile_id: str) -> str:
    try:
        return proxy_change._config(profile_id).get("name") or profile_id
    except (OSError, ValueError):
        return profile_id


def make_inactive(profile_id: str) -> None:
    """The account ready for its proxy to go: no job, browser stopped, or phone user stopped and off screen."""
    try:
        config = proxy_change._config(profile_id)
    except (OSError, ValueError) as exc:
        raise NotReady("the account could not be read") from exc
    reason = proxy_change.job_reason(profile_id, config)
    if reason:
        raise NotReady(reason)
    serial = proxy_change._phone_serial(config)
    if serial:
        _stop_phone_user(serial, int(config["runtime"]["android_user_id"]))
    else:
        _stop_browser(profile_id)


def _stop_browser(profile_id: str) -> None:
    from backend.services import docker_service
    if docker_service.get_container_status(profile_id) not in {"running", "paused"}:
        return
    try:
        asyncio.run(docker_service.run_profile_action(profile_id, "stop"))
    except Exception as exc:
        raise NotReady(f"its browser could not be stopped: {exc}") from exc
    if docker_service.get_container_status(profile_id) in {"running", "paused"}:
        raise NotReady("its browser is still open after stopping it")


def _stop_phone_user(serial: str, user_id: int) -> None:
    from backend.services import android_devices, android_ownership
    from backend.services.adb_client import AdbError
    from backend.services.android_devices import CONTROL_USER_ID
    client = android_devices._client()
    try:
        android_ownership.ensure_primary(serial, client)
        with android_devices.phone_lock(serial):
            if client.current_user(serial) == user_id:
                # The switch stops the user it leaves (android_devices.switch_and_wait).
                state = android_devices.switch_and_wait(client, serial, CONTROL_USER_ID)
                if not state["ready"]:
                    raise NotReady(f"the phone did not go to its main user: {state.get('reason') or 'not ready'}")
            if any(user.user_id == user_id and user.running for user in client.users(serial)):
                client.stop_user(serial, user_id)
            if any(user.user_id == user_id and user.running for user in client.users(serial)):
                raise NotReady("its Android user kept running")
    except NotReady:
        raise
    except ValueError as exc:  # another PC manages the phone
        raise NotReady(str(exc)) from exc
    except AdbError as exc:
        raise NotReady(f"the phone is not ready: {exc}") from exc


def replace_and_move(proxy_id: str, move_pc: Callable[[str, str], object]) -> dict:
    """One proxy: its accounts made inactive, the IP replaced at Webshare, then each account moved."""
    old = next((p for p in proxy_service.load_proxy_pool(raise_errors=True) if p.get("id") == proxy_id), None)
    if not old:
        raise webshare_service.WebshareError("This proxy is no longer in the pool.")
    accounts = proxy_service.holders(old)
    for profile_id in accounts:  # a job on any of them leaves all of them untouched, browsers included
        reason = proxy_change.job_reason(profile_id)
        if reason:
            raise webshare_service.WebshareError(f"Not replaced: {_name(profile_id)} isn't ready, {reason}")
    for profile_id in accounts:
        try:
            make_inactive(profile_id)
        except NotReady as exc:
            raise webshare_service.WebshareError(f"Not replaced: {_name(profile_id)} isn't ready, {exc}.") from exc
    if old.get("replaced_by"):
        new_id = old["replaced_by"]
        if not any(p.get("id") == new_id for p in proxy_service.load_proxy_pool()):
            raise webshare_service.WebshareError("Its replacement is no longer in the pool, so its accounts can't move to it.")
        result = {"proxy_id": proxy_id, "old": f"{old.get('host')}:{old.get('port')}", "new": old.get("replaced_by_address"),
                  "new_id": new_id, "accounts": accounts, "already_replaced": True}
    else:
        result = webshare_service.replace_one(proxy_id)
        new_id = result["new_id"]
    moved, failed = [], []
    for profile_id in accounts:
        try:
            reason = proxy_change.job_reason(profile_id)  # a job may have started while Webshare worked
            if reason:
                raise NotReady(reason)
            proxy_change.change(profile_id, new_id, move_pc)
            moved.append(profile_id)
        except Exception as exc:  # one account failing never stops the others
            failed.append({"profile_id": profile_id, "name": _name(profile_id), "error": str(exc)[:300]})
    still_on_old = next((proxy_service.holders(p) for p in proxy_service.load_proxy_pool() if p.get("id") == proxy_id), [])
    if accounts and not still_on_old:
        proxy_service.delete_proxy(proxy_id)
    return {**result, "moved": moved, "move_failed": failed}
