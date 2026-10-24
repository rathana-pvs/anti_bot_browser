"""Daily Brief reuses the durable creator lifecycle, not a separate export path."""
import io
import json
import shutil
import uuid
from unittest.mock import patch

import pytest
from PIL import Image

from backend.services.reel_creator.models import Composition, Photo, RenderInput
from backend.services.reel_creator.service import ReelCreator
from backend.services.reel_creator.daily_brief import layers, poster, runtime_status


def setup_story(tmp_path):
    creator = ReelCreator(tmp_path/'creator', tmp_path/'media', admission=lambda: None)
    photos = []
    for color, headline in [('#c35138', 'First verified fact'), ('#368fa3', 'Why this matters')]:
        data = io.BytesIO()
        Image.new('RGB', (600, 400), color).save(data, 'PNG')
        asset = creator.import_asset(data.getvalue(), 'photo.png', 'image')
        photos.append(Photo(image_id=asset['id'], headline=headline))
    spec = Composition(version=2, template='daily_brief', photos=photos, image_id=photos[0].image_id,
                       duration=10, fps=24, resolution=720, source='Sample source | 23 October 2026',
                       brand='DAILY BRIEF', category='COMMUNITY', supporting='A short source-based explanation.')
    return creator, spec


def test_layout_validation_and_selected_photo(tmp_path):
    creator, spec = setup_story(tmp_path)
    paths = [creator.asset_path(p.image_id, 'image') for p in spec.photos]
    first, _ = poster(spec, paths)
    second, _ = poster(spec, paths, photo_index=1)
    assert first.size == (540, 960)
    assert first.getpixel((270, 300)) != second.getpixel((270, 300))
    with pytest.raises(ValueError, match='source attribution'):
        poster(spec.model_copy(update={'source':''}), paths)
    with pytest.raises(ValueError, match='vertical'):
        poster(spec.model_copy(update={'aspect_ratio':'1:1'}), paths)
    with pytest.raises(ValueError):
        poster(spec.model_copy(update={'headline':'z'*240, 'photos':[spec.photos[0].model_copy(update={'headline':'z'*240})]}), paths)


def test_missing_runtime_blocks_only_new_layout(tmp_path):
    creator, spec = setup_story(tmp_path)
    with patch('backend.services.reel_creator.daily_brief.runtime_status', return_value=(False, 'Prepare Daily Brief runtime')):
        assert creator.capabilities(refresh=True)['ready']
        assert not creator.capabilities()['daily_brief']
        with pytest.raises(ValueError, match='Prepare Daily Brief'):
            creator.enqueue(RenderInput(request_id=uuid.uuid4().hex, composition=spec))
        legacy = spec.model_copy(update={'template':'newsroom'})
        assert creator.enqueue(RenderInput(request_id=uuid.uuid4().hex, composition=legacy))['state'] == 'queued'


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'), reason='Prepared local Remotion runtime required')
def test_real_preview_uses_journal_verification_and_cancellation(tmp_path):
    creator, spec = setup_story(tmp_path)
    request = RenderInput(request_id=uuid.uuid4().hex, composition=spec, kind='preview', post_caption='Keep this reviewed caption')
    job = creator.enqueue(request)
    assert creator.enqueue(request)['id'] == job['id']
    creator._render(job)
    result = creator.store.job(job['id'])
    assert result['state'] == 'completed', result.get('error')
    assert result['post_caption'] == request.post_caption
    assert result.get('peak_rss_bytes', 0) <= 768*1024*1024
    assert creator.output_path(result).is_file()
    assert not list((creator.root/'staging').iterdir())
    cancelled = creator.enqueue(RenderInput(request_id=uuid.uuid4().hex, composition=spec, kind='preview'))
    creator.store.update(cancelled['id'], cancel_requested=True)
    creator._render(cancelled)
    assert creator.store.job(cancelled['id'])['state'] == 'cancelled'
    assert not list((creator.root/'staging').iterdir())


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'), reason='Prepared local Remotion runtime required')
def test_five_photo_full_export_with_audio_preserves_order_and_budget(tmp_path):
    import subprocess
    creator, spec = setup_story(tmp_path)
    photos = [spec.photos[i % 2] for i in range(5)]
    spec = spec.model_copy(update={'photos':photos, 'duration':15, 'fps':30, 'resolution':1080,
                                  'audio_preset':'headlines', 'slide_seconds':3})
    job = creator.enqueue(RenderInput(request_id=uuid.uuid4().hex, composition=spec, post_caption='Reviewed caption'))
    creator._render(job)
    result = creator.store.job(job['id'])
    assert result['state'] == 'completed', result.get('error')
    assert result.get('peak_rss_bytes', 0) <= 768*1024*1024
    output = creator.output_path(result)
    meta = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
    assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in meta['streams'])
    # Sample the center of each photo card, away from text and transitions.
    for index in range(5):
        raw = subprocess.check_output(['ffmpeg','-v','error','-ss',str(index*3+1),'-i',str(output),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        frame = Image.open(io.BytesIO(raw)).convert('RGB')
        reference = Image.open(creator.asset_path(photos[index].image_id,'image')).convert('RGB').getpixel((300,200))
        assert max(abs(a-b) for a,b in zip(frame.getpixel((540,600)),reference)) < 10
    print(f"Full Daily Brief render peak: {result.get('peak_rss_bytes',0)/1024/1024:.1f} MiB")


def test_daily_card_retains_operator_crop(tmp_path):
    from backend.services.reel_creator.models import CropRect
    creator, spec = setup_story(tmp_path)
    raw = Image.new('RGB', (600,400), '#ff0000')
    raw.paste('#0000ff', (300,0,600,400))
    data=io.BytesIO();raw.save(data,'PNG')
    asset=creator.import_asset(data.getvalue(),'split.png','image')
    photo=Photo(image_id=asset['id'], framing='fit', crop=CropRect(left=.5))
    base, image, text = layers(spec,photo,creator.asset_path(asset['id'],'image'))
    assert image.getpixel((540,600))[:3] == (0,0,255)
    # Fit keeps the narrow retained crop intact, with empty card margins.
    assert image.getpixel((73,600))[3] == 0
    for layer in (base,image,text): layer.close()
