import io
import json
import subprocess

from PIL import Image, ImageChops
import pytest

from backend.services.reel_creator.auto_framing import framing_for_faces, suggest_framing
from backend.services.reel_creator.composition import balanced_headline, fit_news_text, text_width, news_brand, draw_headline, accent_ink
from backend.services.reel_creator.framing import photo_bounds
from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.service import ReelCreator, tool
from backend.services.reel_creator.story import overlays, save_story, story_command


def test_balanced_headlines_preserve_words_and_manual_breaks():
    text = 'City opens new hospital to serve growing communities'
    lines = balanced_headline(text, 72)
    assert ' '.join(lines) == text
    assert 1 < len(lines) <= 3
    assert all(text_width(line, 72) <= 908 for line in lines)
    assert min(text_width(line, 72) for line in lines) > max(text_width(line, 72) for line in lines) * .6
    assert balanced_headline('First line\nSecond line', 72) == ['First line', 'Second line']


def test_clean_text_fits_joint_panel_and_accent_crosses_lines():
    from PIL import ImageDraw
    spec = Composition(news_design='clean', headline='City opens a new hospital', supporting='Doors open on Monday',
                       headline_emphasis='new hospital', category='LOCAL', accent='#facc15')
    lines, size, support, subsize = fit_news_text(spec)
    assert 1430 + len(lines) * round(size * 1.55) + 10 + len(support) * round(subsize * 1.55) <= 1800
    assert accent_ink(spec.accent) == '#111827'
    highlighted = Image.new('RGB', (1080, 500))
    plain = highlighted.copy()
    draw_headline(ImageDraw.Draw(highlighted), (92, 100), ['City opens a new', 'hospital'], 60, spec)
    draw_headline(ImageDraw.Draw(plain), (92, 100), ['City opens a new', 'hospital'], 60, spec.model_copy(update={'headline_emphasis': ''}))
    assert ImageChops.difference(highlighted, plain).crop((0, 0, 1080, 150)).getbbox()
    assert ImageChops.difference(highlighted, plain).crop((0, 150, 1080, 300)).getbbox()
    # Old saved compositions retain their typography and do not gain the new design.
    assert Composition().news_design == 'classic'


def test_brand_is_identical_across_story_layers():
    spec = Composition(news_design='clean', brand='CITY NEWS', source='City council · 21 October', accent='#38bdf8')
    brand = dict(overlays(spec))['brand']
    assert ImageChops.difference(brand, news_brand(spec)).getbbox() is None
    assert brand.getbbox()


def test_clean_legacy_render_keeps_source_above_the_headline_gradient(tmp_path):
    from backend.services.reel_creator.composition import make_layers, poster
    path = tmp_path / 'photo.png'
    Image.new('RGB', (800, 800), 'black').save(path)
    spec = Composition(news_design='clean', headline='City update', source='City council')
    rendered = poster(make_layers(spec, path))
    assert rendered.crop((32, 900, 500, 930)).convert('L').getextrema()[1] > 130


@pytest.mark.parametrize('size,faces', [((1800, 1200), [(1200, 180, 160, 180)]),
                                     ((1800, 1200), [(100, 100, 220, 240), (1400, 150, 220, 240)]),
                                     ((800, 1600), [(320, 1100, 200, 220)])])
def test_auto_framing_preserves_all_faces_above_the_news_panel(size, faces):
    settings = framing_for_faces(size, faces)
    photo = Photo(image_id='1' * 32, **settings)
    spec = Composition(headline='City news')
    _, (x, y, w, h) = photo_bounds(spec, photo, size)
    for left, top, width, height in faces:
        assert 0 <= x + left / size[0] * w
        assert x + (left + width) / size[0] * w <= 1080
        assert 0 <= y + top / size[1] * h
        assert y + (top + height) / size[1] * h <= 1210
    assert photo.movement == 'still'


def test_no_faces_and_unavailable_detector_keep_the_whole_photo(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'cv2', None)
    result = suggest_framing(Image.new('RGB', (1200, 600)))
    assert result['status'] == 'unavailable'
    assert result['settings']['framing'] == 'fit'
    assert framing_for_faces((1200, 600), [])['framing'] == 'fit'


def test_import_persists_local_suggestion_and_clean_design_encodes(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    try:
        data = io.BytesIO()
        Image.new('RGB', (1200, 800), '#315c78').save(data, 'PNG')
        asset = service.import_asset(data.getvalue(), 'city.png', 'image')
        assert asset['suggested_framing']['settings']['framing'] == 'fit'
        assert service.store.asset(asset['id'])['suggested_framing'] == asset['suggested_framing']
        spec = Composition(version=2, photos=[Photo(image_id=asset['id'], **asset['suggested_framing']['settings'])],
                           image_id=asset['id'], news_design='clean', headline='City opens new hospital',
                           headline_emphasis='new hospital', brand='CITY NEWS', category='LOCAL NEWS',
                           source='City council', accent='#facc15', duration=5)
        image, _ = service.render_poster(spec)
        assert Image.open(io.BytesIO(image)).size == (540, 960)
        if not tool('ffmpeg'):
            pytest.skip('FFmpeg unavailable')
        images, text, _ = save_story(spec, [service.asset_path(asset['id'], 'image')], None, tmp_path / 'layers')
        output = tmp_path / 'news.mp4'
        subprocess.run(story_command(tool('ffmpeg'), images, text, output, spec, preview=True), check=True, capture_output=True, timeout=60)
        probe = json.loads(subprocess.check_output([tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
        stream = probe['streams'][0]
        assert (stream['width'], stream['height']) == (360, 640)
        assert float(stream['duration']) == pytest.approx(5, abs=.1)
    finally:
        service.stop()
