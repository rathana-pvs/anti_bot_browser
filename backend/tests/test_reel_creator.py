"""Isolated creator checks: no Docker, accounts, cloud, or publication."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
import psutil
import pytest

from backend.routers import reel_creator as router
from backend.security import DesktopSessionAuthMiddleware
from backend.services.reel_creator.composition import encode_command, make_layers, save_layers, wrap
from backend.services.reel_creator.models import Composition, RenderInput
from backend.services.reel_creator.service import ReelCreator, Interrupted, stop_owned_process, tool
from backend.services.reel_creator.store import Conflict


def image_bytes(size=(960, 540), color='#346482', fmt='PNG'):
    data = io.BytesIO()
    Image.new('RGB', size, color).save(data, fmt)
    return data.getvalue()


@pytest.fixture
def creator(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    yield service
    service.stop()


@pytest.fixture
def spec(creator):
    asset = creator.import_asset(image_bytes(), '../news.png', 'image')
    return Composition(image_id=asset['id'], headline='Today’s news', category='UPDATE', brand='LOCAL NEWS')


def test_import_decodes_content_and_normalizes_orientation(creator):
    raw = Image.new('RGB', (80, 40))
    exif = raw.getexif()
    exif[274] = 6
    data = io.BytesIO()
    raw.save(data, 'JPEG', exif=exif)
    asset = creator.import_asset(data.getvalue(), '../../private.jpg', 'image')
    assert asset['name'] == 'private.jpg'
    assert (asset['width'], asset['height']) == (40, 80)
    with Image.open(creator.asset_path(asset['id'], 'image')) as stored:
        assert stored.format == 'PNG' and stored.size == (40, 80)
    with pytest.raises(ValueError):
        creator.import_asset(b'not an image', 'pretend.png', 'image')
    with pytest.raises(ValueError):
        creator.import_asset(image_bytes(), 'logo.jpg', 'audio')
    assert not any(p.suffix == '.audio' for p in (creator.root / 'assets').iterdir())


@pytest.mark.parametrize('fmt', ['JPEG', 'PNG', 'WEBP', 'AVIF', 'GIF', 'BMP', 'TIFF'])
def test_common_image_formats_are_decoded_and_normalized_to_png(creator, fmt):
    # Detection uses file content, including misleading/double extensions.
    asset = creator.import_asset(image_bytes(fmt=fmt), 'download.jpg.webp', 'image')
    assert asset['name'] == 'download.jpg.webp'
    assert (asset['width'], asset['height']) == (960, 540)
    with Image.open(creator.asset_path(asset['id'], 'image')) as stored:
        assert stored.format == 'PNG' and stored.mode == 'RGB'
    assert creator.thumbnail_path(asset['id']).is_file()


def test_webp_keeps_pixel_limits_and_png_only_logos(creator, monkeypatch):
    from backend.services.reel_creator import service
    data = image_bytes(size=(40, 40), fmt='WEBP')
    with pytest.raises(ValueError, match='PNG logo'):
        creator.import_asset(data, 'logo.webp', 'logo')
    monkeypatch.setattr(service, 'MAX_PIXELS', 1000)
    with pytest.raises(ValueError, match='exceeds 20 million pixels'):
        creator.import_asset(data, 'large.webp', 'image')


@pytest.mark.parametrize('fmt', ['WEBP', 'GIF', 'PNG', 'TIFF'])
def test_animated_and_multipage_images_do_not_create_assets(creator, fmt):
    data = io.BytesIO()
    frames = [Image.new('RGB', (30, 30), color) for color in ('red', 'blue')]
    frames[0].save(data, fmt, save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(ValueError, match='still image'):
        creator.import_asset(data.getvalue(), 'animation.' + fmt.lower(), 'image')
    with pytest.raises(ValueError, match='could not be decoded'):
        creator.import_asset(b'not a WebP image', 'broken.webp', 'image')
    assert not list((creator.root / 'assets').glob('*.png'))


def test_input_limits_paths_and_invalid_settings(creator):
    with pytest.raises(ValidationError):
        Composition(image_id='../../profiles/private')
    with pytest.raises(ValidationError):
        Composition(accent='red;movie=private')
    with pytest.raises(ValidationError):
        Composition(focal_x=float('nan'))
    with pytest.raises(ValueError):
        creator.import_asset(b'x' * (25 * 1024 * 1024 + 1), 'huge.png', 'image')
    with pytest.raises(ValueError):
        creator.import_asset(image_bytes((5000, 4100)), 'pixels.png', 'image')
    with pytest.raises(ValueError):
        creator.validate_assets(Composition())


def test_audio_playlist_is_rejected_before_any_subprocess(creator, monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Playlist reached a subprocess'))
    with pytest.raises(ValueError, match='Playlists'):
        creator.import_asset(b'#EXTM3U\nhttps://example.com/private.ts', 'sound.mp3', 'audio')


def test_draft_revisions_prevent_lost_edits(creator, spec):
    draft_id = uuid.uuid4().hex
    first = creator.store.save_draft(draft_id, 0, spec.model_dump())
    assert first['revision'] == 1
    changed = spec.model_copy(update={'headline': 'Edited locally'}).model_dump()
    creator.store.save_draft(draft_id, 1, changed)
    with pytest.raises(Conflict):
        creator.store.save_draft(draft_id, 1, spec.model_dump())
    assert creator.store.drafts()[0]['composition']['headline'] == 'Edited locally'


def test_concurrent_duplicate_requests_and_claims(creator, spec):
    def submit(_):
        return creator.store.create_job('same-request', 'same-snapshot', spec.model_dump(), 'final')
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(submit, range(12)))
    assert len({j['id'] for j in jobs}) == 1
    with pytest.raises(Conflict):
        creator.store.create_job('same-request', 'different-snapshot', spec.model_dump(), 'final')
    with ThreadPoolExecutor(max_workers=3) as pool:
        claims = list(pool.map(lambda _: creator.store.claim(), range(3)))
    assert sum(value is not None for value in claims) == 1
    spec.headline = 'Draft changed later'
    assert creator.store.job(jobs[0]['id'])['composition']['headline'] != spec.headline


def test_queued_cancel_and_retry_are_transactional(creator, spec):
    job = creator.store.create_job('request', 'snapshot', spec.model_dump(), 'final')
    creator.store.cancel(job['id'])
    assert creator.store.claim() is None
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: creator.retry(job['id']), range(3)))
    claimed = creator.store.claim()
    assert claimed['id'] == job['id'] and claimed['attempt'] == 1
    with pytest.raises(Conflict):
        creator.retry(job['id'])


@pytest.mark.parametrize('template', ['newsroom', 'editorial', 'brief'])
@pytest.mark.parametrize('treatment', ['fit', 'fill'])
def test_actual_layout_handles_mixed_khmer_and_preserves_image(creator, spec, template, treatment):
    spec.template, spec.treatment = template, treatment
    spec.headline = 'ព័ត៌មានថ្មីៗ\nLocal news 2026'
    spec.supporting = 'A short supporting line'
    layers = make_layers(spec, creator.asset_path(spec.image_id, 'image'))
    assert all(layer.size == (1080, 1920) for layer in layers[:3])
    assert layers[2].getbbox() is not None
    image, warnings = creator.render_poster(spec)
    assert Image.open(io.BytesIO(image)).size == (540, 960)
    assert warnings == []
    assert creator.render_poster(spec)[0] == image
    if treatment == 'fit':
        # Central source pixels are preserved in the stable foreground layer.
        assert layers[1].getpixel((540, 700))[:3] == (52, 100, 130)


def test_text_overflow_is_explicit_and_zero_width_marks_are_accepted(creator, spec):
    spec.headline = 'ក' * 200
    with pytest.raises(ValueError, match='line break|shorten|Shorten'):
        creator.render_poster(spec)
    spec.headline = 'ខ្មែរ\nNews'
    spec.supporting = 'A\u200b B'
    assert creator.render_poster(spec)[0]
    with pytest.raises(ValueError, match='cannot display'):
        wrap('Unsupported 😀', 50, 900, 3)


def test_render_process_cancel_stops_owned_process_and_pid_reuse_is_rejected(creator, spec):
    job = creator.store.create_job('cancel', 'snapshot', spec.model_dump(), 'final')
    job = creator.store.claim()
    directory = creator.root / 'staging' / 'cancel-test'
    directory.mkdir(parents=True)
    job['workdir'] = str(directory)
    creator.store.update(job['id'], workdir=str(directory))
    command = [sys.executable, '-c', 'import time; time.sleep(60)', str(directory / 'owned-token')]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(creator._run, job, command, 'encode')
        deadline = time.monotonic() + 5
        while not creator.store.job(job['id']).get('process') and time.monotonic() < deadline:
            time.sleep(0.02)
        record = creator.store.job(job['id'])['process']
        assert record
        assert not stop_owned_process({**record, 'created': record['created'] + 10}, str(directory))
        assert psutil.pid_exists(record['pid'])
        creator.store.cancel(job['id'])
        with pytest.raises(Interrupted, match='cancelled'):
            future.result(timeout=8)
    assert not psutil.pid_exists(record['pid'])


def test_restart_marks_unfinished_work_interrupted_and_preserves_queued(creator, spec):
    first = creator.store.create_job('active', 'one', spec.model_dump(), 'final')
    creator.store.claim()
    creator.store.create_job('queued', 'two', spec.model_dump(), 'final')
    creator._recover()
    assert creator.store.job(first['id'])['state'] == 'interrupted'
    assert creator.store.claim()['state'] == 'rendering'


def test_final_renders_take_priority_and_preview_expiry_preserves_exports(creator, spec):
    preview = creator.store.create_job('preview', 'one', spec.model_dump(), 'preview')
    final = creator.store.create_job('final', 'two', spec.model_dump(), 'final')
    assert creator.store.claim()['id'] == final['id']
    previews = creator.root / 'previews'
    previews.mkdir()
    preview_path = previews / f'reel_{preview["id"]}.mp4'
    preview_path.write_bytes(b'old preview')
    creator.store.update(preview['id'], state='completed', output={'filename': preview_path.name})
    # Set the old timestamp directly: ordinary updates always refresh it.
    with creator.store.db() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (preview['id'],)).fetchone()
        payload = json.loads(row['payload'])
        payload['updated_at'] = time.time() - 8 * 86400
        db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(payload), preview['id']))
    creator.media_dir.mkdir()
    final_path = creator.media_dir / f'reel_{final["id"]}.mp4'
    final_path.write_bytes(b'keep this export')
    creator.store.update(final['id'], state='completed', output={'filename': final_path.name})
    creator._expire_previews()
    assert not preview_path.exists() and creator.store.job(preview['id'])['state'] == 'failed'
    assert final_path.read_bytes() == b'keep this export'


def test_recovery_finds_an_orphan_spawned_before_pid_persistence(creator, spec):
    job = creator.store.create_job('orphan', 'snapshot', spec.model_dump(), 'final')
    creator.store.claim()
    directory = creator.root / 'staging' / 'orphan-attempt'
    directory.mkdir(parents=True)
    creator.store.update(job['id'], workdir=str(directory), process=None)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             cwd=directory, start_new_session=os.name == 'posix')
    try:
        creator._recover()
        child.wait(timeout=5)
        assert not psutil.pid_exists(child.pid)
        assert creator.store.job(job['id'])['state'] == 'interrupted'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_api_is_authenticated_and_does_not_offer_incomplete_output(creator, spec, monkeypatch):
    monkeypatch.setattr(router, 'get_service', lambda: creator)
    monkeypatch.setenv('MANAGER_API_TOKEN', 'creator-test')
    app = FastAPI()
    app.add_middleware(DesktopSessionAuthMiddleware)
    app.include_router(router.router)
    client = TestClient(app)
    assert client.get('/api/reel-creator/drafts').status_code == 403
    client.headers['X-Manager-Token'] = 'creator-test'
    response = client.post('/api/reel-creator/poster', json=spec.model_dump())
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    job = creator.store.create_job('api', 'snapshot', spec.model_dump(), 'final')
    assert client.get(f'/api/reel-creator/jobs/{job["id"]}/download').status_code == 409
    details = client.get(f'/api/reel-creator/jobs/{job["id"]}').json()
    assert 'process' not in details and 'workdir' not in details
    assert client.put('/api/reel-creator/drafts/not-a-valid-id', json={'revision': 0, 'composition': spec.model_dump()}).status_code == 422


@pytest.mark.skipif(not tool('ffmpeg') or not tool('ffprobe'), reason='Host FFmpeg/FFprobe not installed')
def test_real_export_audio_decode_and_promotion_recovery(creator, spec):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 8000)
    asset = creator.import_asset(data.getvalue(), 'one-second.wav', 'audio')
    spec.audio_id, spec.motion = asset['id'], False
    creator.start()
    job = creator.enqueue(RenderInput(composition=spec, request_id=str(uuid.uuid4())))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = creator.store.job(job['id'])
        if job['state'] not in ('queued', 'rendering', 'verifying'):
            break
        time.sleep(0.2)
    creator.stop()
    assert job['state'] == 'completed', job
    path = creator.output_path(job)
    metadata = json.loads(subprocess.check_output([tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
    assert {s['codec_name'] for s in metadata['streams']} == {'h264', 'aac'}
    assert abs(float(metadata['streams'][0]['duration']) - 10) < 0.1
    assert list(creator.media_dir.iterdir()) == [path]
    # Simulate a crash after rename but before the job's completion transaction.
    creator.store.update(job['id'], state='verifying', output=None)
    creator.stop_event.clear()
    creator._recover()
    recovered = creator.store.job(job['id'])
    assert recovered['state'] == 'completed' and recovered['output']['filename'] == path.name
    assert list(creator.media_dir.iterdir()) == [path]


@pytest.mark.skipif(not tool('ffmpeg') or not tool('ffprobe'), reason='Host FFmpeg/FFprobe not installed')
@pytest.mark.parametrize('template', ['editorial', 'brief'])
def test_real_motion_filter_graph_for_other_templates(creator, spec, tmp_path, template):
    spec.template = template
    layers = make_layers(spec, creator.asset_path(spec.image_id, 'image'))
    paths = save_layers(layers, tmp_path / 'layers')
    output = tmp_path / 'clip.mp4'
    command = encode_command(tool('ffmpeg'), paths, output, spec, preview=True)
    command[command.index('-t') + 1] = '0.7'
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    result = json.loads(subprocess.check_output([tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
    assert (result['streams'][0]['width'], result['streams'][0]['height']) == (360, 640)


@pytest.mark.skipif(not tool('ffmpeg') or not tool('ffprobe'), reason='Host FFmpeg/FFprobe not installed')
def test_builtin_news_audio_renders_and_custom_audio_takes_precedence(creator, spec, tmp_path):
    from backend.services.reel_creator.audio_presets import preset_path
    spec.audio_preset = 'politics'
    image, logo, audio = creator.validate_assets(spec)
    assert audio == preset_path('politics')
    layers = save_layers(make_layers(spec, image, logo), tmp_path / 'layers')
    output = tmp_path / 'news-with-audio.mp4'
    command = encode_command(tool('ffmpeg'), layers, output, spec, audio, preview=True)
    command[command.index('-t') + 1] = '1'
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    metadata = json.loads(subprocess.check_output([tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
    assert {stream['codec_name'] for stream in metadata['streams']} == {'h264', 'aac'}
    custom = creator.import_asset(preset_path('headlines').read_bytes(), 'custom.wav', 'audio')
    spec.audio_id = custom['id']
    assert creator.validate_assets(spec)[2] == creator.asset_path(custom['id'], 'audio')
