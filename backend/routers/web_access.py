from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services.web_access import WebAccessError, web_access
from backend.services.web_gateway import GATEWAY_HEADER

router = APIRouter(prefix="/api/system/web-access", tags=["system"])


class WebAccessRequest(BaseModel):
    enabled: bool


def _from_web(request: Request) -> bool:
    return request.headers.get(GATEWAY_HEADER) == "1"


@router.get("")
async def get_web_access(request: Request):
    status = web_access.status()
    if _from_web(request):
        # The sign-in details are shown only on this PC.
        status["password"] = None
    status["opened_from_web"] = _from_web(request)
    return status


@router.post("")
async def set_web_access(body: WebAccessRequest, request: Request):
    if _from_web(request):
        raise HTTPException(status_code=403, detail="Turn web access on or off from the app on this PC.")
    try:
        if body.enabled:
            port = request.scope.get("server", (None, None))[1]
            if not isinstance(port, int):
                raise WebAccessError("The app could not find its own address. Restart the app and try again.")
            status = await web_access.turn_on(port)
        else:
            status = await web_access.turn_off()
    except WebAccessError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {**status, "opened_from_web": False}


@router.post("/password")
async def new_web_access_password(request: Request):
    if _from_web(request):
        raise HTTPException(status_code=403, detail="Make a new password from the app on this PC.")
    try:
        status = await web_access.new_password()
    except WebAccessError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {**status, "opened_from_web": False}
