"""Voiceover: Gemini text-to-speech, local word timing, captions that follow the voice, and the mixed video."""
import base64
import io
import json
import math
import struct
import subprocess
import time
import wave
from types import SimpleNamespace

import httpx
import pytest

from backend.services.ai import AIConfig
from backend.services.ai.speech import pcm_to_wav, synthesize, wav_seconds
from backend.services.reel_creator.composition import Composition, CompositionPlan, CScene
from backend.services.reel_creator.service import ReelCreator, narration, tool, voice_mix
from backend.services.reel_creator.voice_timing import caption_chunks, find_pauses, word_timings

GEMINI = AIConfig(model='gemini-3.8-flash', api_key='AIzaSyTESTKEY0000000000000000000')


@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


def tone(seconds=1.0, gaps=(), rate=24000):
    """A WAV of a tone, silent during each (start, end) gap."""
    frames = bytearray()
    for n in range(int(seconds * rate)):
        t = n / rate
        quiet = any(a <= t < b for a, b in gaps)
        frames += struct.pack('<h', 0 if quiet else int(9000 * math.sin(2 * math.pi * 220 * t)))
    return pcm_to_wav(bytes(frames), rate)


def gemini(body, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=body)
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), timeout=kwargs.get('timeout'))


def audio_reply(data):
    return {'steps': [{'type': 'model_output', 'content': [{'type': 'audio', 'data': base64.b64encode(data).decode()}]}]}


def test_gemini_speech_request_and_reply():
    seen, wav = [], tone(.5)
    assert synthesize(GEMINI, 'Metro Line 4 gets the green light.', voice='Puck', style='calm', client_factory=gemini(audio_reply(wav), seen=seen)) == wav
    sent = seen[0]
    assert sent['model'] == 'gemini-3.8-flash-lite-tts' and sent['generation_config']['speech_config'] == [{'voice': 'Puck'}]
    assert sent['input'][0]['content'][0]['annotations'][0]['style'] == 'calm' and sent['response_format'] == {'type': 'audio'}
    pcm = b'\x00\x01' * 2400  # headerless PCM is wrapped as WAV
    out = synthesize(GEMINI, 'Hello there.', client_factory=gemini(audio_reply(pcm)))
    assert out[:4] == b'RIFF' and abs(wav_seconds(out) - .1) < .01


def test_gemini_speech_errors_are_plain():
    with pytest.raises(ValueError, match='rejected the API key'):
        synthesize(GEMINI, 'Hi.', client_factory=gemini({'error': {'message': 'API key not valid.'}}, status=400))
    with pytest.raises(ValueError, match='no audio'):
        synthesize(GEMINI, 'Hi.', client_factory=gemini({'steps': []}))
    with pytest.raises(ValueError, match='Gemini key'):
        synthesize(AIConfig(provider='claude', model='claude-opus-5-5', api_key='k'), 'Hi.')
    with pytest.raises(ValueError, match='voices'):
        synthesize(GEMINI, 'Hi.', voice='Nobody')


def test_word_timings_cover_the_clip_and_meet_pauses():
    text = 'The council voted on Tuesday. Construction starts in March.'
    plain = word_timings(text, 4.0)
    assert [w for w, _, _ in plain] == text.split() and plain[0][1] == 0 and abs(plain[-1][2] - 4.0) < .01
    assert all(a[2] <= b[1] + 1e-6 for a, b in zip(plain, plain[1:]))
    paused = word_timings(text, 4.0, [(1.6, 2.0)])
    end_of_first = next(t for t in paused if t[0] == 'Tuesday.')
    assert abs(end_of_first[2] - 1.6) < .01 and abs(paused[5][1] - 2.0) < .01
    chunks = caption_chunks(paused)
    assert [words for words, _, _ in chunks] == [['The', 'council', 'voted'], ['on', 'Tuesday.'], ['Construction', 'starts', 'in', 'March.']]


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg is not installed')
def test_pauses_are_found_in_audio(tmp_path):
    path = tmp_path / 'voice.wav'
    path.write_bytes(tone(2.0, gaps=[(.8, 1.2)]))
    pauses = find_pauses(path, tool('ffmpeg'))
    assert len(pauses) == 1 and abs(pauses[0][0] - .8) < .06 and abs(pauses[0][1] - 1.2) < .06


def test_narration_reads_the_scene_words():
    assert narration(CScene(kind='stat', stat='20 minutes', headline='Saved on the trip')) == '20 minutes. Saved on the trip.'
    assert narration(CScene(kind='quote', headline='This changes everything', attribution='Mayor Lina Chan')) == \
        'Mayor Lina Chan said: “This changes everything.”'
    assert narration(CScene(headline='Line 4 approved', detail='Work starts in March')) == 'Line 4 approved. Work starts in March.'


def test_voice_mix_ducks_music_under_the_voice():
    with_music = voice_mix(True, [0.25, 4.5], 'afade=t=in:d=0.5')
    assert 'adelay=250' in with_music and 'adelay=4500' in with_music and 'sidechaincompress' in with_music and with_music.endswith('[mix]')
    assert 'sidechaincompress' not in voice_mix(False, [0.25], 'afade=t=in:d=0.5')


@pytest.fixture
def creator(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    yield service
    service.stop()


def composition():
    return Composition(recipe='clean', scenes=[CScene(headline='Metro Line 4 gets the green light', detail='The council voted 7 to 2.'),
                                               CScene(kind='end', headline='Would you ride it?')])


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg is not installed')
def test_voiceover_is_made_once_timed_and_captioned(creator):
    calls = []

    def fake(config, text, voice, model):
        calls.append(text)
        return tone(1.5 + len(text) / 40)
    result = creator.add_voiceover(GEMINI, composition(), synthesize=fake)
    voiced = Composition(**result['composition'])
    assert result['characters'] == sum(len(t) for t in calls) and len(calls) == 2 and voiced.captions == 'voice'
    first = voiced.scenes[0]
    assert first.voice and first.voice.words and first.to_scene().length() >= first.voice.seconds + .25
    again = creator.add_voiceover(GEMINI, voiced, synthesize=fake)
    assert again['characters'] == 0 and len(calls) == 2  # same words, same voice: reused
    tracks = voiced.voice_tracks()
    assert tracks[0][1] == .25 and tracks[1][1] > tracks[0][1] + first.voice.seconds
    plan = CompositionPlan(voiced, 360, {})
    spoken = plan.frame(plan.starts[0] + .25 + first.voice.words[1][1] + .01)
    quiet = CompositionPlan(voiced.model_copy(update={'captions': 'off'}), 360, {}).frame(plan.starts[0] + .25 + first.voice.words[1][1] + .01)
    assert spoken.tobytes() != quiet.tobytes()  # the caption is drawn


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg is not installed')
def test_rendered_video_carries_the_voiceover(creator):
    from backend.services.reel_creator.composition import DocumentRenderInput
    voiced = Composition(**creator.add_voiceover(GEMINI, composition().model_copy(update={'music': 'off'}),
                                                 synthesize=lambda c, t, voice, model: tone(1.2))['composition'])
    creator.start()
    job = creator.enqueue(DocumentRenderInput(request_id='9' * 32, reel=voiced, kind='preview'))
    end = time.time() + 90
    while creator.store.job(job['id'])['state'] not in ('completed', 'failed') and time.time() < end:
        time.sleep(.2)
    done = creator.store.job(job['id'])
    assert done['state'] == 'completed', done['error']
    probe = json.loads(subprocess.run([tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(creator.output_path(done))],
                                      capture_output=True, text=True, check=True).stdout)
    assert any(s['codec_type'] == 'audio' for s in probe['streams'])
