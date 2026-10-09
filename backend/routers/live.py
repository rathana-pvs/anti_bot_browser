from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.services import live_service as live

router = APIRouter(prefix='/api/profiles/{profile_id}/live', tags=['live'])


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_url: str = Field(max_length=256)
    stream_key: str = Field(default='', max_length=512)
    producer_url: str = Field(default='https://www.facebook.com/live/producer/', max_length=2048)


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=255)
    title: str = Field(default='', max_length=255)
    caption: str = Field(default='', max_length=5000)
    pinned_comment: str | None = Field(default=None, max_length=1000)
    loop: bool = False
    muted: bool = False
    max_duration_seconds: int | None = Field(default=None, ge=1, le=live.MAX_VIDEO_SECONDS)


class Review(BaseModel):
    confirmed_ended: bool


def call(fn, *args):
    try:
        return fn(*args)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None


@router.get('/settings')
async def settings(profile_id: str):
    return call(live.get_settings, profile_id)


@router.put('/settings')
async def save_settings(profile_id: str, payload: Settings):
    return call(live.save_settings, profile_id, payload.model_dump())


@router.get('/status')
async def status(profile_id: str):
    return call(live.get_status, profile_id)


@router.post('/start')
async def start(profile_id: str, payload: Start):
    try:
        return await live.start_live(profile_id, payload.filename, payload.loop, payload.max_duration_seconds, payload.muted, payload.title, payload.caption, payload.pinned_comment)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None


@router.post('/stop')
async def stop(profile_id: str, execution_id: str | None = None):
    return call(live.stop_live, profile_id, execution_id)


@router.post('/review')
async def review(profile_id: str, payload: Review):
    if not payload.confirmed_ended:
        raise HTTPException(400, 'Confirm on Facebook that the previous broadcast has ended')
    return call(live.clear_review, profile_id)
