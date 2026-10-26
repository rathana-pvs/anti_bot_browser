"""Background-removal caching, inference contract, and preview/export integration."""
import io

import numpy as np
import pytest
from PIL import Image

from backend.services.reel_creator import cutout
from backend.services.reel_creator.reel import Reel, Scene
from backend.services.reel_creator.service import ReelCreator


def test_existing_cutout_bypasses_model_and_preserves_alpha(tmp_path, monkeypatch):
    source = tmp_path / 'portrait.png'
    Image.new('RGBA', (30, 40), (200, 60, 30, 140)).save(source)
    monkeypatch.setattr(cutout, '_segment', lambda *args: pytest.fail('Already transparent'))
    assert cutout.prepare_cutout(source, tmp_path / 'cache') == source
    assert not (tmp_path / 'cache').exists()


def test_generated_cutout_is_cached_and_original_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / 'portrait.jpg'
    Image.new('RGB', (100, 160), '#b12a34').save(source)
    original = source.read_bytes()
    calls = []

    def segment(image, directory):
        calls.append(image.size)
        image.putalpha(100)
        return image

    monkeypatch.setattr(cutout, '_segment', segment)
    result = cutout.prepare_cutout(source, tmp_path / 'cache')
    assert result != source and result.is_file()
    assert cutout.prepare_cutout(source, tmp_path / 'cache') == result
    assert calls == [(100, 160)] and source.read_bytes() == original
    with Image.open(result) as image:
        assert image.mode == 'RGBA' and image.getpixel((0, 0))[3] == 100
    # A replaced source must never reuse the old subject mask.
    Image.new('RGB', (100, 160), '#11ff00').save(source)
    assert cutout.prepare_cutout(source, tmp_path / 'cache') != result
    assert len(calls) == 2


def test_failed_segmentation_is_not_cached_and_can_retry(tmp_path, monkeypatch):
    source = tmp_path / 'photo.png'
    Image.new('RGB', (40, 40), 'red').save(source)
    monkeypatch.setattr(cutout, '_segment', lambda *args: (_ for _ in ()).throw(RuntimeError('bad model')))
    with pytest.raises(ValueError, match='could not remove'):
        cutout.prepare_cutout(source, tmp_path / 'cache')
    assert not list(tmp_path.rglob('*v1.png'))
    monkeypatch.setattr(cutout, '_segment', lambda image, directory: image)
    assert cutout.prepare_cutout(source, tmp_path / 'cache').is_file()


def test_inference_uses_rgb_normalization_and_rejects_empty_predictions(tmp_path, monkeypatch):
    class Net:
        def setInput(self, value):
            assert value.shape == (1, 3, 320, 320) and value.dtype == np.float32
            assert value[0, 0, 0, 0] > value[0, 1, 0, 0]  # RGB red stays the first channel
        def forward(self):
            mask = np.zeros((1, 1, 320, 320), dtype=np.float32)
            mask[:, :, 80:240, 80:240] = 1
            return mask

    net = Net()
    monkeypatch.setattr(cutout, '_net', net)
    result = cutout._segment(Image.new('RGB', (80, 100), 'red'), tmp_path)
    assert result.getpixel((0, 0))[3] == 0 and result.getpixel((40, 50))[3] == 255
    monkeypatch.setattr(net, 'forward', lambda: np.zeros((1, 1, 320, 320), dtype=np.float32))
    with pytest.raises(ValueError, match='foreground subject'):
        cutout._segment(Image.new('RGB', (80, 100), 'red'), tmp_path)


def test_model_download_checks_integrity_and_reuses_verified_file(tmp_path, monkeypatch):
    data = b'test weights'
    checksum = cutout.hashlib.sha256(data).hexdigest()
    calls = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def raise_for_status(self):
            pass
        def iter_bytes(self):
            calls.append(True)
            yield data

    monkeypatch.setattr(cutout, 'MODEL_SHA256', checksum)
    monkeypatch.setattr(cutout.httpx, 'stream', lambda *args, **kwargs: Response())
    target = cutout._model_path(tmp_path)
    assert target.read_bytes() == data
    assert cutout._model_path(tmp_path) == target and len(calls) == 1
    target.write_bytes(b'corrupt model')
    assert cutout._model_path(tmp_path).read_bytes() == data and len(calls) == 2
    monkeypatch.setattr(cutout, 'MODEL_SHA256', 'invalid')
    target.unlink()
    with pytest.raises(ValueError, match='download is invalid'):
        cutout._model_path(tmp_path)
    assert not target.exists()


def test_pop_art_prepares_selected_preview_and_all_export_images_only(tmp_path, monkeypatch):
    service = ReelCreator(tmp_path / 'studio', tmp_path / 'media')
    ids = ['a' * 32, 'b' * 32]
    images = {key: tmp_path / f'{key}.png' for key in ids}
    calls = []

    def prepare(path, cache):
        calls.append(path)
        return path.with_suffix('.cutout.png')

    monkeypatch.setattr(cutout, 'prepare_cutout', prepare)
    reel = Reel(look='pop_art', scenes=[Scene(headline='First', image_id=ids[0]), Scene(headline='Second', image_id=ids[1])])
    preview = service._prepare_images(reel, images, 1)
    assert calls == [images[ids[1]]] and preview[ids[0]] == images[ids[0]]
    assert preview[ids[1]].name.endswith('.cutout.png')
    calls.clear()
    exported = service._prepare_images(reel, images)
    assert set(calls) == set(images.values())
    assert all(path.name.endswith('.cutout.png') for path in exported.values())
    calls.clear()
    reel.look = 'clean'
    assert service._prepare_images(reel, images) is images and not calls


def test_poster_uses_automatic_cutout_path(tmp_path, monkeypatch):
    service = ReelCreator(tmp_path / 'studio', tmp_path / 'media')
    data = io.BytesIO()
    Image.new('RGB', (100, 120), 'red').save(data, 'PNG')
    asset = service.import_asset(data.getvalue(), 'photo.png', 'image')
    calls = []

    def segment(image, directory):
        calls.append(True)
        image.putalpha(Image.new('L', image.size, 0))
        image.paste((200, 200, 200, 255), (30, 20, 70, 120))
        return image

    monkeypatch.setattr(cutout, '_segment', segment)
    reel = Reel(look='pop_art', scenes=[Scene(headline='Headline', image_id=asset['id'])])
    assert Image.open(io.BytesIO(service.render_poster(reel))).size == (540, 960)
    assert len(calls) == 1
    reel.scenes[0].headline = 'Edited headline'
    service.render_poster(reel)
    assert len(calls) == 1  # editing text does not repeat inference
