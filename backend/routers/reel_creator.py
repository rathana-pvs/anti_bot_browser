import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse, Response

from backend.services.reel_creator.models import Composition, CropRect, DraftInput, RenderInput
from backend.services.reel_creator.service import MAX_UPLOAD, get_service
from backend.services.reel_creator.store import Conflict
from backend.services.reel_creator.news_ai import NewsRequest, generate_news
from backend.services.reel_creator.template_news import TemplateNewsRequest, generate_template_news
from backend.services.reel_creator.content_fit import check_content_fit
from backend.services.reel_creator.articles import extract_article
from backend.services.reel_creator.audio_presets import PRESETS, preset_path
from pydantic import BaseModel, Field
from backend.services.reel_creator.narration import ScriptRequest, VoiceRequest, generate_script, EdgeSpeechProvider, voice_fingerprint

router = APIRouter(prefix='/api/reel-creator', tags=['reel-creator'])
Id = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]
_voice_lock = asyncio.Lock()


@router.post('/narration/script')
def narration_script(value: ScriptRequest):
    return checked(generate_script, value)


@router.post('/narration/voice')
async def narration_voice(value: VoiceRequest):
    if _voice_lock.locked():
        raise HTTPException(409, 'Another voice is being prepared. Try again shortly.')
    async with _voice_lock:
        service = get_service()
        fingerprint = voice_fingerprint(value.text, value.voice, value.rate)
        # Reuse durable local audio across retries, reloads, previews and renders.
        with service.store.db() as db:
            rows = db.execute('SELECT payload FROM assets WHERE payload LIKE ?', ('%' + fingerprint + '%',)).fetchall()
        for row in rows:
            asset = json.loads(row['payload'])
            if asset.get('narration', {}).get('fingerprint') == fingerprint:
                try:
                    service.asset_path(asset['id'], 'audio')
                    return asset
                except ValueError:
                    pass
        try:
            audio = await EdgeSpeechProvider().synthesize(value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return await asyncio.to_thread(checked, service.import_asset, audio, 'English narration.mp3', 'audio',
                                       narration=dict(provider='edge', fingerprint=fingerprint))


@router.get('/narration/{asset_id}/audio')
def narration_audio(asset_id: Id):
    service = get_service()
    asset = checked(service.store.asset, asset_id, 'audio')
    if not asset.get('narration'):
        raise HTTPException(400, 'This asset is not a narration recording.')
    return FileResponse(checked(service.asset_path, asset_id, 'audio'), media_type='audio/mpeg')


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
    return checked(get_service().store.save_draft, draft_id, value.revision, value.composition.model_dump(),
                   value.editorial.model_dump() if value.editorial is not None else None)


class ArticleInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


@router.post('/article')
def article(value: ArticleInput):
    return checked(extract_article, value.url)


@router.post('/content-fit')
def content_fit(value: Composition):
    return checked(check_content_fit, value)


@router.post('/generate-template-news')
def template_news(value: TemplateNewsRequest):
    return checked(generate_template_news, value)


@router.post('/generate-news')
def generate(value: NewsRequest):
    return checked(generate_news, value)


@router.get('/audio-presets')
def audio_presets():
    return [{'id': key, **value} for key, value in PRESETS.items()]


@router.get('/audio-presets/{preset}/preview')
def preview_audio(preset: str):
    return FileResponse(checked(preset_path, preset), media_type='audio/wav')


@router.post('/assets/{kind}')
async def import_asset(kind: str, file: UploadFile = File(...)):
    if kind not in ('image', 'logo', 'audio'):
        raise HTTPException(400, 'Choose image, logo, or audio.')
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


@router.get('/assets/{asset_id}/framing')
def auto_framing(asset_id: Id, aspect_ratio: Literal['9:16', '4:5', '1:1'] = '9:16'):
    from PIL import Image
    from backend.services.reel_creator.auto_framing import suggest_framing
    path = checked(get_service().asset_path, asset_id, 'image')
    with Image.open(path) as image:
        return suggest_framing(image, aspect_ratio)


@router.post('/assets/{asset_id}/framing')
def cropped_auto_framing(asset_id: Id, crop: CropRect, aspect_ratio: Literal['9:16', '4:5', '1:1'] = '9:16'):
    from PIL import Image
    from backend.services.reel_creator.auto_framing import suggest_framing
    from backend.services.reel_creator.framing import crop_image
    path = checked(get_service().asset_path, asset_id, 'image')
    with Image.open(path) as image:
        return suggest_framing(crop_image(image, crop), aspect_ratio)


@router.get('/assets/{asset_id}/crop-preview')
def crop_preview(asset_id: Id):
    import io
    from PIL import Image, ImageOps
    path = checked(get_service().asset_path, asset_id, 'image')
    with Image.open(path) as image:
        preview = image.convert('RGB')
        preview.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        preview.save(output, 'JPEG', quality=90)
    return Response(output.getvalue(), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.post('/poster')
def poster(value: Composition, photo_index: int = Query(default=0, ge=0, le=4)):
    image, warnings = checked(get_service().render_poster, value, photo_index)
    return Response(image, media_type='image/jpeg', headers={
        'Cache-Control': 'no-store', 'X-Reel-Warnings': json.dumps(warnings)})


@router.get('/brand')
def brand():
    return get_service().store.brand()


@router.put('/brand')
def save_brand(value: Composition):
    if value.logo_id:
        checked(get_service().asset_path, value.logo_id, 'logo')
    return get_service().store.brand({key: getattr(value, key) for key in ('brand', 'accent', 'logo_id')})


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


@router.get('/jobs/{job_id}/download')
def download(job_id: Id):
    job = checked(get_service().store.job, job_id)
    if job['kind'] != 'final':
        raise HTTPException(409, 'Render a full-quality video before exporting.')
    path = checked(get_service().output_path, job)
    return FileResponse(path, media_type='video/mp4', filename=path.name)
