"""Hardware is optional; exercise both fallback paths and real GPU when available."""
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageStat

from backend.services.reel_creator import acceleration
from backend.services.reel_creator import service
from backend.services.reel_creator.reel import Reel, RenderInput, Scene
from backend.tests.test_reel_studio import wait


@pytest.mark.parametrize('failure', ['initialize', 'resize'])
def test_photo_gpu_failure_uses_identical_cpu_output_and_stops_retrying(monkeypatch, failure):
    calls, closed = [], []

    class BrokenScaler:
        def __init__(self):
            calls.append('initialize')
            if failure == 'initialize':
                raise RuntimeError('No usable GPU')

        def resize(self, *args):
            calls.append('resize')
            raise RuntimeError('GPU lost')

        def close(self):
            closed.append(True)

    monkeypatch.setattr(acceleration, '_local', SimpleNamespace())
    monkeypatch.setattr(acceleration, 'GpuScaler', BrokenScaler)
    image = Image.effect_noise((100, 180), 40).convert('RGB')
    box, size = (3.1, 4.2, 93.4, 175.5), (90, 170)
    expected = image.resize(size, Image.Resampling.BILINEAR, box=box).tobytes()
    for _ in range(2):
        assert acceleration.resize_photo(image, size, box).tobytes() == expected
    assert calls == (['initialize'] if failure == 'initialize' else ['initialize', 'resize'])
    assert closed == ([] if failure == 'initialize' else [True])


def test_software_opengl_is_rejected(monkeypatch):
    import moderngl
    released = []
    monkeypatch.setattr(moderngl, 'create_standalone_context', lambda **kw: SimpleNamespace(
        info={'GL_RENDERER': 'llvmpipe'}, release=lambda: released.append(True)))
    with pytest.raises(RuntimeError, match='software'):
        acceleration.GpuScaler()
    assert released == [True]


def test_real_gpu_scaling_matches_cpu_orientation_crop_and_filtering():
    try:
        scaler = acceleration.GpuScaler()
    except Exception as exc:
        pytest.skip(f'Hardware OpenGL unavailable: {exc}')
    try:
        image = Image.effect_noise((420, 740), 50).convert('RGB')
        # Mark opposite corners to expose a vertical flip or a stale cached texture.
        image.paste((255, 0, 0), (0, 0, 210, 100))
        image.paste((0, 0, 255), (210, 640, 420, 740))
        for size, box in [((360, 640), (3.2, 8.1, 411.4, 733.5)),
                          ((360, 640), (9, 16, 369, 656))]:
            gpu = scaler.resize(image, size, box)
            cpu = image.resize(size, Image.Resampling.BILINEAR, box=box)
            assert max(ImageStat.Stat(ImageChops.difference(gpu, cpu)).mean) < 1
        # Reusing a thread across unrelated poster requests must not reuse old photos.
        other = Image.new('RGB', image.size, (5, 100, 240))
        assert scaler.resize(other, size, box).getpixel((100, 100)) == (5, 100, 240)
    finally:
        scaler.close()


@pytest.mark.parametrize('outcome', ['success', 'failure', 'timeout'])
def test_nvenc_requires_a_successful_real_encode_and_caches_result(tmp_path, monkeypatch, outcome):
    creator = service.ReelCreator(tmp_path / 'creator', tmp_path / 'media')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[command.index('-s') + 1] == '256x256'
        assert len(kwargs['input']) == 256 * 256 * 3
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 10)
        return SimpleNamespace(returncode=0 if outcome == 'success' else 1)

    monkeypatch.setattr(service, 'tool', lambda name: '/ffmpeg')
    monkeypatch.setattr(service.subprocess, 'run', run)
    assert creator._use_nvenc() == (outcome == 'success')
    assert creator._use_nvenc() == (outcome == 'success')
    assert len(calls) == 1
    creator.stop()


@pytest.mark.parametrize('kind', ['preview', 'final'])
def test_nvenc_failure_restarts_on_cpu_and_verifies_output(tmp_path, monkeypatch, kind):
    creator = service.ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    creator._nvenc = True
    original = service.subprocess.Popen
    attempts = []

    def popen(command, *args, **kwargs):
        if '-c:v' in command:
            codec = command.index('-c:v') + 1
            attempts.append(command[codec])
            if command[codec] == 'h264_nvenc':
                command = list(command)
                # Force the actual encoder subprocess to exit while frames are piped.
                command[codec] = 'unavailable_test_encoder'
        return original(command, *args, **kwargs)

    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    creator.start()
    try:
        reel = Reel(music='off', scenes=[Scene(headline='GPU fallback check', seconds=2)])
        job = wait(creator, creator.enqueue(RenderInput(request_id='a' * 32, reel=reel, kind=kind))['id'])
        assert job['state'] == 'completed', job['error']
        assert attempts == ['h264_nvenc', 'libx264']
        assert creator._nvenc is False
    finally:
        creator.stop()


def test_cancelled_gpu_render_never_restarts_on_cpu(tmp_path, monkeypatch):
    creator = service.ReelCreator(tmp_path / 'creator', tmp_path / 'media')
    creator._nvenc = True
    calls = []

    def cancelled(*args):
        calls.append(True)
        raise service.Interrupted('Render cancelled.')

    monkeypatch.setattr(creator, '_encode_attempt', cancelled)
    with pytest.raises(service.Interrupted, match='cancelled'):
        creator._encode({}, None, {}, None, None, tmp_path / 'out.mp4')
    assert len(calls) == 1
    creator.stop()
