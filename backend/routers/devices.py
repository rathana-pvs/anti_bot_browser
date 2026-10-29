import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from backend.security import websocket_token_valid
from backend.services import android_devices, android_live, android_network
from backend.services.adb_client import AdbError, AdbUnavailable


router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)


class UserImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    group: str = Field(default="", max_length=80)
    proxy_id: str | None = Field(default=None, min_length=1, max_length=120)


class TimezoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    timezone: str = Field(min_length=1, max_length=64)


class DeviceSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    one_account_at_a_time: bool | None = None
    switch_from_app_only: bool | None = None


class SwitchUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(ge=0)


def _call(action):
    try:
        return action()
    except android_devices.DeviceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdbUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_devices():
    return android_devices.list_devices()


@router.post("/{serial}/scan")
def scan_device(serial: str):
    return _call(lambda: android_devices.scan_device(serial))


@router.put("/{serial}")
def rename_device(serial: str, request: DeviceRenameRequest):
    return _call(lambda: android_devices.rename_device(serial, request.label))


@router.delete("/{serial}")
def forget_device(serial: str):
    _call(lambda: android_devices.forget_device(serial))
    return {"success": True}


@router.post("/{serial}/switch-user")
def switch_user(serial: str, request: SwitchUserRequest):
    return _call(lambda: android_devices.switch_user(serial, request.user_id))


@router.get("/accounts/{profile_id}/network")
def account_network(profile_id: str):
    return _call(lambda: android_network.network_status(profile_id))


@router.post("/accounts/{profile_id}/network/verify")
def verify_account_network(profile_id: str):
    return _call(lambda: android_network.verify(profile_id))


@router.post("/accounts/{profile_id}/open")
def open_account(profile_id: str):
    return _call(lambda: android_network.open_account(profile_id))


@router.put("/{serial}/settings")
def update_device_settings(serial: str, request: DeviceSettingsRequest):
    return _call(lambda: android_devices.update_device_settings(
        serial, request.one_account_at_a_time, request.switch_from_app_only))


@router.post("/{serial}/timezone")
def set_timezone(serial: str, request: TimezoneRequest):
    return _call(lambda: android_devices.set_timezone(serial, request.timezone))


@router.post("/{serial}/users/{user_id}/import", status_code=201)
def import_user(serial: str, user_id: int, request: UserImportRequest):
    return _call(lambda: android_devices.import_user(serial, user_id, request.name, request.group, request.proxy_id))


@router.websocket("/{serial}/live")
async def live_view(websocket: WebSocket, serial: str, user_id: int | None = None, access_token: str = ""):
    """Bridge the phone's screen and input. Text frames carry JSON, binary frames scrcpy video packets."""
    if not websocket_token_valid(access_token or websocket.headers.get("x-manager-token", "")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    session = None
    try:
        if user_id is not None:
            await websocket.send_json({"type": "status", "message": "Switching the phone to this user…"})
            state = await run_in_threadpool(android_devices.switch_user, serial, user_id)
            if not state["ready"]:
                await websocket.send_json({"type": "error", "message": state["reason"]})
                return
        await websocket.send_json({"type": "status", "message": "Starting the screen stream…"})
        session = await android_live.open_session(serial)
        await websocket.send_json({"type": "meta", "device_name": session.device_name, **(session.size or {})})
        await _bridge(websocket, session)
    except (android_devices.DeviceNotFound, AdbError, android_live.LiveViewError, ValueError) as exc:
        await _send_error(websocket, str(exc))
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            await android_live.close_session(session)
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


async def _bridge(websocket: WebSocket, session: android_live.LiveSession) -> None:
    async def video():
        while True:
            await websocket.send_bytes(await session.read_packet())

    async def control():
        while True:
            try:
                await session.send(json.loads(await websocket.receive_text()))
            except (ValueError, KeyError, TypeError):
                continue  # Ignore malformed input instead of dropping the view.

    tasks = [asyncio.create_task(video()), asyncio.create_task(control()),
             asyncio.create_task(session.drain_device_messages())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception()
            if isinstance(error, asyncio.IncompleteReadError) or (error is None and task is tasks[2]):
                await _send_error(websocket, "The phone stopped streaming. It may have been disconnected.")
            elif error and not isinstance(error, WebSocketDisconnect):
                raise error
    finally:
        for task in tasks:
            task.cancel()


async def _send_error(websocket: WebSocket, message: str) -> None:
    try:
        await websocket.send_json({"type": "error", "message": message})
    except (RuntimeError, WebSocketDisconnect):
        pass
