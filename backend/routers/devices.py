from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.services import android_devices
from backend.services.adb_client import AdbError, AdbUnavailable


router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)


class UserImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    group: str = Field(default="", max_length=80)


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


@router.post("/{serial}/users/{user_id}/import", status_code=201)
def import_user(serial: str, user_id: int, request: UserImportRequest):
    return _call(lambda: android_devices.import_user(serial, user_id, request.name, request.group))
