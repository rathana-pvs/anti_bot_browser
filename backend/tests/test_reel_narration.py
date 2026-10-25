import asyncio
import io
import json
import math
import struct
import subprocess
import time
import uuid
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.routers import reel_creator as router
from backend.services.reel_creator.models import Composition, Photo, RenderInput
from backend.services.reel_creator.narration import ScriptRequest, VoiceRequest, generate_script, mix_command, voice_fingerprint, EdgeSpeechProvider
from backend.services.reel_creator.service import ReelCreator, tool


def recording(seconds=3, frequency=400, speech=False):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<h', round(11000 * math.sin(2 * math.pi * frequency * n / 48000)))
                                  if not speech or .5 < n / 48000 < 1.5 else b'\0\0' for n in range(int(seconds * 48000))))
    return data.getvalue()


@pytest.fixture
def creator(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    yield service
    service.stop()


def test_source_grounded_script_has_duration_budget_and_no_caption_material():
    request = ScriptRequest(api_key='test-key', model='gemini-test', text='The city council announced a new hospital. Doors will open on Monday. ' * 3, duration=10)
    result = generate_script(request, lambda _: {'script':'A new hospital will open on Monday, the city council announced.', 'source_quotes':['The city council announced a new hospital.']})
    assert result['word_limit'] == 19
    assert result['script'].startswith('A new hospital')
    for response in [dict(script='An election was held with 900 votes.', source_quotes=['The city council announced a new hospital.']),
                     dict(script='The hospital opens Monday.', source_quotes=['This passage was invented by the model.']),
                     dict(script=' '.join(['news'] * 40), source_quotes=['The city council announced a new hospital.']),
                     dict(script='Read the #News headline.', source_quotes=['The city council announced a new hospital.'])]:
        with pytest.raises(ValueError):
            generate_script(request, lambda _: response)


def test_voice_model_and_fingerprints_are_bounded():
    assert voice_fingerprint('hello ', 'en-US-GuyNeural', '+0%') == voice_fingerprint('hello', 'en-US-GuyNeural', '+0%')
    assert voice_fingerprint('hello', 'en-US-GuyNeural', '+0%') != voice_fingerprint('hello', 'en-US-JennyNeural', '+0%')
    for data in [dict(text='x'),dict(text='News report text.',voice='km-KH-SreymomNeural'),dict(text='News report text.',rate='+500%')]:
        with pytest.raises(ValueError): VoiceRequest(**data)


def test_voice_api_caches_audio_and_preview_is_local(creator, monkeypatch):
    monkeypatch.setattr(router, 'get_service', lambda: creator)
    calls = []
    async def synthesize(self, request):
        calls.append(request.text)
        return recording()
    monkeypatch.setattr(EdgeSpeechProvider, 'synthesize', synthesize)
    app = FastAPI(); app.include_router(router.router)
    with TestClient(app) as client:
        payload = dict(text='The city council announced a new hospital.')
        response = client.post('/api/reel-creator/narration/voice', json=payload)
        assert response.status_code == 200
        asset = response.json()
        assert asset['duration'] == pytest.approx(3, abs=.05)
        assert client.post('/api/reel-creator/narration/voice', json=payload).json()['id'] == asset['id']
        assert len(calls) == 1
        preview = client.get(f'/api/reel-creator/narration/{asset["id"]}/audio')
        assert preview.status_code == 200 and preview.content.startswith(b'RIFF')
        assert client.post('/api/reel-creator/narration/voice', json={**payload,'voice':'not-a-voice'}).status_code == 422


def test_stale_and_overlong_audio_rejected_but_disabled_voice_keeps_saved_work(creator):
    request = VoiceRequest(text='The city council announced a new hospital.')
    asset = creator.import_asset(recording(), 'voice.wav', 'audio', narration={'fingerprint':voice_fingerprint(request.text, request.voice, request.rate)})
    spec = Composition(narration_enabled=True,narration_text=request.text,narration_id=asset['id'])
    assert creator.validate_narration(spec).is_file()
    for changes in [dict(narration_text='A completely different news report.'),dict(narration_voice='en-US-JennyNeural'),dict(narration_rate='+10%'),dict(narration_id=None)]:
        with pytest.raises(ValueError):creator.validate_narration(spec.model_copy(update=changes))
    long_asset = creator.import_asset(recording(seconds=6), 'long.wav', 'audio', narration=asset['narration'])
    with pytest.raises(ValueError,match='longer video'):
        creator.validate_narration(spec.model_copy(update=dict(duration=5,narration_id=long_asset['id'])))
    assert creator.validate_narration(spec.model_copy(update=dict(narration_enabled=False,narration_text='changed'))) is None


def test_mix_ducks_music_during_speech_and_keeps_voice_when_music_is_zero(tmp_path):
    if not tool('ffmpeg'):pytest.skip('FFmpeg unavailable')
    import numpy as np
    voice, music, output = (tmp_path / name for name in ('voice.wav','music.wav','mix.wav'))
    voice.write_bytes(recording(speech=True)); music.write_bytes(recording(frequency=1000))
    spec = Composition(duration=5, volume=.5,narration_enabled=True)
    subprocess.run(mix_command(tool('ffmpeg'),voice,music,output,spec),check=True,capture_output=True,timeout=20)
    with wave.open(str(output)) as audio:
        pcm=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').reshape(-1,2)[:,0]/32768
    def amplitude(start):
        n=np.arange(4800); segment=pcm[int(start*48000):int(start*48000)+4800]
        return abs(np.sum(segment*np.exp(-2j*np.pi*1000*n/48000)))*2/4800
    assert amplitude(.9)<amplitude(.1)*.7
    for bed in (None,music):
        subprocess.run(mix_command(tool('ffmpeg'),voice,bed,output,spec.model_copy(update={'volume':0})),check=True,capture_output=True,timeout=20)
        with wave.open(str(output)) as audio:
            pcm=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2')/32768
        assert np.max(abs(pcm))>.2


@pytest.mark.parametrize('version',[1,2])
def test_actual_render_has_audible_narration_with_music_disabled(creator, version):
    request = VoiceRequest(text='The city council announced a new hospital.')
    voice = creator.import_asset(recording(), 'voice.wav', 'audio', narration={'fingerprint':voice_fingerprint(request.text, request.voice, request.rate)})
    data=io.BytesIO();Image.new('RGB',(800,600),'#315c78').save(data,'PNG')
    photo=creator.import_asset(data.getvalue(),'city.png','image')
    spec=Composition(version=version,image_id=photo['id'],photos=[Photo(image_id=photo['id'])] if version==2 else [],
                     headline='City opens new hospital',duration=5,audio_preset='none',volume=0,narration_enabled=True,
                     narration_text=request.text,narration_id=voice['id'])
    creator.start()
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,kind='preview'))
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        job=creator.store.job(job['id'])
        if job['state'] in ('completed','failed'):break
        time.sleep(.1)
    assert job['state']=='completed',job.get('error')
    output=creator.root/'previews'/f'reel_{job["id"]}.mp4'
    meta=json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(output)]))
    assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in meta['streams'])
    assert float(meta['format']['duration'])==pytest.approx(5,abs=.2)
