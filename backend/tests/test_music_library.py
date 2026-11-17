"""My music: added tracks are trimmed, level-matched to the built-in tracks, and play once when they are intros."""
import json
import math
import struct
import subprocess
import time
from types import SimpleNamespace

import pytest

from backend.services.ai.speech import pcm_to_wav
from backend.services.reel_creator.composition import Composition, CScene, DocumentRenderInput
from backend.services.reel_creator.service import ReelCreator, checked_source, tool, voice_mix

pytestmark = pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg is not installed')


@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


@pytest.fixture
def creator(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    yield service
    service.stop()


def loud_track(seconds, silence=1.0, rate=24000, level=30000):
    """A loud tone after `silence` seconds of nothing, as a downloaded news sting would be."""
    frames = bytearray()
    for n in range(int((seconds + silence) * rate)):
        t = n / rate
        frames += struct.pack('<h', 0 if t < silence else int(level * math.sin(2 * math.pi * 330 * t)))
    return pcm_to_wav(bytes(frames), rate)


def loudness(path):
    out = subprocess.run([tool('ffmpeg'), '-hide_banner', '-nostats', '-i', str(path), '-af', 'ebur128', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    return float(out.rsplit('I:', 1)[1].split('LUFS')[0])


def test_an_added_track_is_trimmed_level_matched_and_listed(creator):
    original = creator.import_asset(loud_track(8), 'breakzstudios-breaking-news-intro-logo-2-314321.mp3', 'audio')
    entry = creator.add_music(original['id'], 'https://pixabay.com/sound-effects/musical-breaking-news-intro-logo-2-314321/')
    assert entry['role'] == 'intro' and abs(entry['seconds'] - 8) < .3  # the second of silence is gone
    assert abs(loudness(creator.asset_path(entry['id'], 'audio')) - -23) < 1.5
    assert loudness(creator.asset_path(original['id'], 'audio')) > -12  # the original file is untouched
    assert [m['id'] for m in creator.music_library()] == [entry['id']]
    assert creator.update_music(entry['id'], role='bed')['role'] == 'bed'
    creator.remove_music(entry['id'])
    assert creator.music_library() == [] and creator.asset_path(entry['id'], 'audio').is_file()  # reels using it still render


def test_long_tracks_play_under_the_whole_reel(creator):
    entry = creator.add_music(creator.import_asset(loud_track(26, silence=0), 'theme.mp3', 'audio')['id'])
    assert entry['role'] == 'bed'


def test_where_a_track_came_from_is_a_plain_link():
    assert checked_source(' https://pixabay.com/sound-effects/x-1/ ') == 'https://pixabay.com/sound-effects/x-1/'
    assert checked_source('') == ''
    for bad in ('pixabay.com/x', 'javascript:alert(1)', 'http://pixabay.com/x'):
        with pytest.raises(ValueError):
            checked_source(bad)


def test_an_intro_is_padded_so_the_voice_runs_on():
    assert 'apad' in voice_mix(True, [.25], 'afade=t=in:d=0.5', loops=False)
    assert 'apad' not in voice_mix(True, [.25], 'afade=t=in:d=0.5')


def test_a_reel_with_an_intro_plays_it_once_and_renders_in_full(creator):
    entry = creator.add_music(creator.import_asset(loud_track(3), 'sting.mp3', 'audio')['id'])
    reel = Composition(music='custom', music_id=entry['id'], scenes=[CScene(headline='Metro Line 4 gets the green light', seconds=4),
                                                                     CScene(kind='end', headline='Would you ride it?', seconds=3)])
    assert creator.music_loops(reel) is False
    creator.start()
    job = creator.enqueue(DocumentRenderInput(request_id='5' * 32, reel=reel, kind='preview'))
    end = time.time() + 90
    while creator.store.job(job['id'])['state'] not in ('completed', 'failed') and time.time() < end:
        time.sleep(.2)
    done = creator.store.job(job['id'])
    assert done['state'] == 'completed', done['error']
    path = creator.output_path(done)
    late = subprocess.run([tool('ffmpeg'), '-hide_banner', '-nostats', '-ss', '5', '-i', str(path), '-af', 'volumedetect', '-vn', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr
    assert 'max_volume: -91' in late or 'max_volume: -inf' in late  # after the sting: silence, not the sting again
    probe = json.loads(subprocess.run([tool('ffprobe'), '-v', 'error', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True).stdout)
    assert abs(float(probe['format']['duration']) - 7) < .2
