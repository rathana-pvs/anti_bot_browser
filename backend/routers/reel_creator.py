import asyncio
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from backend.services.reel_creator.articles import extract_article
from backend.services.reel_creator.audio_presets import preset_path
from backend.services.reel_creator.reel import MAX_HOUSE_STYLES, DraftInput, HouseStyle, Reel, RenderInput
from backend.services.reel_creator.service import MAX_UPLOAD, get_service
from backend.services.reel_creator.store import Conflict
from backend.services.reel_creator.writer import WriteRequest, write_reel

router = APIRouter(prefix='/api/reel-creator', tags=['reel-creator'])
Id = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]


def checked(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def public(job):
    return {key: value for key, value in job.items() if key not in ('process', 'workdir')}


@router.get('/capabilities')
def capabilities(refresh: bool = False):
    return get_service().capabilities(refresh)


@router.get('/drafts')
def drafts():
    return get_service().store.drafts()


@router.put('/drafts/{draft_id}')
def save_draft(draft_id: Id, value: DraftInput):
    return checked(get_service().store.save_draft, draft_id, value.revision,
                   dict(reel=value.reel.model_dump(), caption=value.caption, hooks=value.hooks, goal=value.goal, comment=value.comment,
                        source_text=value.source_text, source_url=value.source_url,
                        source_title=value.source_title, source_publisher=value.source_publisher))


@router.delete('/drafts/{draft_id}')
def delete_draft(draft_id: Id):
    get_service().store.delete_draft(draft_id)
    return {}


class ArticleInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


@router.post('/article')
def article(value: ArticleInput):
    return checked(extract_article, value.url)


@router.post('/write')
def write(value: WriteRequest):
    return checked(write_reel, value)


@router.post('/assets/{kind}')
async def import_asset(kind: str, file: UploadFile = File(...)):
    if kind not in ('image', 'logo', 'audio'):
        raise HTTPException(400, 'Choose image, logo or audio.')
    data = bytearray()
    try:
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, 'Choose a file no larger than 25 MB.')
        return await asyncio.to_thread(checked, get_service().import_asset, bytes(data), file.filename or 'asset', kind)
    finally:
        await file.close()


@router.get('/assets/{asset_id}')
def asset(asset_id: Id):
    value = checked(get_service().store.asset, asset_id)
    return {key: item for key, item in value.items() if key != 'filename'}


@router.get('/assets/{asset_id}/thumbnail')
def thumbnail(asset_id: Id):
    return FileResponse(checked(get_service().thumbnail_path, asset_id), media_type='image/jpeg')


@router.post('/poster')
def poster(value: Reel, scene: int = Query(default=0, ge=0, le=7)):
    image = checked(get_service().render_poster, value, scene)
    return Response(image, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.post('/photo')
def photo(value: Reel, scene: int = Query(default=0, ge=0, le=7), ratio: str = Query(default='4:5')):
    return checked(get_service().create_photo, value, scene, ratio)


@router.get('/photos/{photo_id}/download')
def download_photo(photo_id: Id):
    path = get_service().photo_path(photo_id)
    if not path.is_file():
        raise HTTPException(404, 'This photo is no longer available. Make it again.')
    return FileResponse(path, media_type='image/jpeg', filename=path.name)


@router.post('/layout')
def layout(value: Reel, scene: int = Query(default=0, ge=0, le=7)):
    return checked(get_service().scene_layout, value, scene)


@router.get('/music/{track}/preview')
def preview_music(track: str):
    return FileResponse(checked(preset_path, track), media_type='audio/wav')


@router.get('/house-styles')
def house_styles():
    return get_service().store.house_styles()


@router.put('/house-styles/{style_id}')
def save_house_style(style_id: Id, value: HouseStyle):
    if value.id != style_id:
        raise HTTPException(400, 'The house style ID does not match the address.')
    return checked(get_service().store.save_house_style, value.model_dump(), MAX_HOUSE_STYLES)


@router.delete('/house-styles/{style_id}')
def delete_house_style(style_id: Id):
    return get_service().store.delete_house_style(style_id)


@router.get('/brand')
def brand():
    return get_service().store.brand()


@router.put('/brand')
def save_brand(value: Reel):
    if value.logo_id:
        checked(get_service().asset_path, value.logo_id, 'logo')
    return get_service().store.brand(dict(brand=value.brand, logo_id=value.logo_id, source=value.source))


@router.get('/jobs')
def jobs():
    return [public(j) for j in get_service().store.jobs()[:50]]


@router.post('/jobs')
def render(value: RenderInput):
    return public(checked(get_service().enqueue, value))


@router.get('/jobs/{job_id}')
def job(job_id: Id):
    return public(checked(get_service().store.job, job_id))


@router.post('/jobs/{job_id}/cancel')
def cancel(job_id: Id):
    return public(checked(get_service().store.cancel, job_id))


@router.post('/jobs/{job_id}/retry')
def retry(job_id: Id):
    return public(checked(get_service().retry, job_id))


@router.get('/jobs/{job_id}/video')
def video(job_id: Id):
    job = checked(get_service().store.job, job_id)
    path = checked(get_service().output_path, job)
    return FileResponse(path, media_type='video/mp4', headers={'Cache-Control': 'no-store'})


@router.get('/jobs/{job_id}/thumbnail')
def job_thumbnail(job_id: Id):
    service = get_service()
    path = checked(service.job_thumbnail_path, checked(service.store.job, job_id))
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.get('/jobs/{job_id}/download')
def download(job_id: Id):
    job = checked(get_service().store.job, job_id)
    if job['kind'] != 'final':
        raise HTTPException(409, 'Render a full-quality video before exporting.')
    path = checked(get_service().output_path, job)
    return FileResponse(path, media_type='video/mp4', filename=path.name)
