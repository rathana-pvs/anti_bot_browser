"""Simple-mode reel studio: document, renderer, AI writer, local render queue."""
import io
import json
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.routers import reel_creator as router
from backend.services.reel_creator.reel import LOOKS, Reel, Scene
from backend.services.reel_creator.renderer import OPENING_COVER_HOLD, OPENING_COVER_SECONDS, Plan
from backend.services.reel_creator.service import ReelCreator
from backend.services.reel_creator.store import Conflict
from backend.services.reel_creator.writer import WriteRequest, write_reel

ARTICLE = ('The city council voted 7 to 2 on Tuesday to approve a new flood relief plan covering three provinces. '
           'Officials said payments will begin next week after the cabinet signs the order.')


def image_bytes(size=(960, 540), color='#346482'):
    data = io.BytesIO()
    Image.new('RGB', size, color).save(data, 'PNG')
    return data.getvalue()


@pytest.fixture
def creator(tmp_path):
    service = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    yield service
    service.stop()


@pytest.fixture
def reel(creator):
    asset = creator.import_asset(image_bytes(), '../news.png', 'image')
    return Reel(look='breaking', brand='LOCAL NEWS', source='City Wire', scenes=[
        Scene(headline='Flood relief plan approved for three provinces', detail='Payments begin next week.',
              image_id=asset['id'], seconds=3),
        Scene(headline='Council vote was 7 to 2', seconds=2)])


# --- document -------------------------------------------------------------

def test_scene_time_follows_reading_length_within_limits():
    assert Scene(headline='Short').length() == 3.5
    assert Scene(headline=' '.join(['word'] * 20), detail=' '.join(['word'] * 25)).length() == 8
    assert Scene(headline='x', seconds=5).length() == 5


def test_reel_defaults_come_from_the_look():
    for name, look in LOOKS.items():
        value = Reel(look=name)
        assert value.tag_text == look['tag'] and value.accent_color == look['accent']
    custom = Reel(look='hot', tag='', accent='#ABCDEF')
    assert custom.tag_text == '' and custom.accent_color == '#abcdef'


def test_reel_rejects_bad_input():
    with pytest.raises(ValidationError):
        Reel(scenes=[])
    with pytest.raises(ValidationError):
        Reel(scenes=[Scene()] * 9)
    with pytest.raises(ValidationError):
        Reel(accent='red')
    with pytest.raises(ValidationError):
        Reel(scenes=[Scene(headline='x', seconds=20)] * 5)  # 90 seconds is the ceiling
    with pytest.raises(ValidationError):
        Reel(ratio='1:1')
    with pytest.raises(ValidationError):
        Reel(music='nonsense')
    with pytest.raises(ValidationError):
        Reel(music='custom')  # a custom track needs its file
    with pytest.raises(ValidationError):
        Reel(music='off', music_id='a' * 32)
    with pytest.raises(ValidationError):
        Scene(image_id='../../etc/passwd')


# --- renderer -------------------------------------------------------------

@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
@pytest.mark.parametrize('look', list(LOOKS))
def test_every_look_renders_in_both_ratios(look, ratio, creator, reel):
    reel.look, reel.ratio = look, ratio
    reel.scenes.append(Scene(headline='Café owners’ “résumé” — naïve plan?', detail='Münich & São Paulo: 50% growth', seconds=3))
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 360, images, logo)
    for index in range(3):
        frame = plan.frame(plan.settled(index))
        assert frame.size == reel.size(360)
    assert plan.frame(0).size == plan.frame(plan.duration).size == reel.size(360)
    assert reel.size(360) == ((360, 640) if ratio == '9:16' else (360, 450))


def test_text_animates_in_and_progress_advances(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 360, images, logo)
    start, settled, near_end = plan.frame(0), plan.frame(plan.settled(0)), plan.frame(plan.duration - .05)
    assert start.tobytes() != settled.tobytes()
    top = lambda frame: frame.crop((0, 0, 360, 4)).getpixel((300, 1))
    assert top(settled) != top(near_end)  # progress fill has reached further


@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
@pytest.mark.parametrize('look', list(LOOKS))
def test_first_frame_is_three_second_still_and_remaining_frames_are_unchanged(look, ratio, creator, reel):
    reel.look, reel.ratio = look, ratio
    reel.scenes[0].seconds = 2  # shortest scene must still have a stable opening and a transition
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 360, images, logo)
    first = plan.frame(0)
    assert first.tobytes() == plan.frame(OPENING_COVER_SECONDS).tobytes()
    # The opening is a composed card, not just the background awaiting text.
    assert first.tobytes() != plan.background(0, 0).tobytes()
    for number in range(1, round(OPENING_COVER_HOLD * 30)):
        assert plan.frame(number / 30).tobytes() == plan._timeline_frame(number / 30).tobytes()
    assert plan.frame(OPENING_COVER_HOLD + .2).tobytes() != first.tobytes()
    assert plan.duration == reel.duration == 4
    assert plan.locate(2) == (1, 0)
    # Later cards keep their entrance animation.
    assert plan.frame(2).tobytes() != plan.frame(plan.settled(1)).tobytes()


@pytest.mark.parametrize('seconds', [2, 3])
def test_short_video_opening_uses_settled_card(seconds, creator, reel):
    reel.scenes = [reel.scenes[0].model_copy(update=dict(seconds=seconds))]
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 360, images, logo)
    assert plan.frame(0).tobytes() == plan.frame(plan.settled(0)).tobytes()
    assert plan.duration == seconds


def test_overflowing_text_names_the_scene(creator):
    value = Reel(scenes=[Scene(headline='ok'), Scene(headline='W' * 120)])
    with pytest.raises(ValueError, match='Scene 2'):
        creator.validate_assets(value)


def test_headline_is_required_somewhere(creator):
    with pytest.raises(ValueError, match='headline'):
        creator.validate_assets(Reel(scenes=[Scene()]))


# --- AI writer ------------------------------------------------------------

def request(**extra):
    return WriteRequest(api_key='k', model='gemini-test', text=ARTICLE + ' ' * 5 + 'x' * 0, publisher='City Wire',
                        source_url='https://example.com/story', **extra)


def copy(**changes):
    value = dict(
        scenes=[dict(headline='Council backs flood relief plan', detail='Vote was 7 to 2',
                     source_quote='voted 7 to 2 on Tuesday to approve a new flood relief plan'),
                dict(headline='Payments start next week', source_quote='payments will begin next week')],
        hook_alternatives=['Who gets flood relief money?', 'Three provinces get help'],
        look='breaking', post_caption='The council approved a flood relief plan. ' * 3, hashtags=['#Flood', 'relief'])
    value.update(changes)
    return value


def test_writer_returns_hooks_scenes_and_caption_with_source():
    result = write_reel(request(caption=True), provider=lambda r: copy())
    assert result['hooks'][0] == 'Council backs flood relief plan' and len(result['hooks']) == 3
    assert result['scenes'][1]['headline'] == 'Payments start next week'
    assert 'http' not in result['caption'] and '#Flood' in result['caption']      # links never ride in the caption
    assert result['comment'] == ''                                                  # engage reels have no link comment
    # Urgency labels are the operator's choice; the model asked for breaking but none was requested.
    assert result['look'] == 'clean'
    assert write_reel(request(look='hot'), provider=lambda r: copy())['look'] == 'hot'
    # The caption is only written on request; scenes and hooks never wait for it.
    plain = write_reel(request(), provider=lambda r: copy())
    assert plain['caption'] == '' and plain['scenes']
    with pytest.raises(ValueError):
        write_reel(request(caption=True), provider=lambda r: copy(post_caption=''))


def test_writer_rejects_ungrounded_drafts_and_retries_once():
    calls = []

    def provider(r):
        calls.append(1)
        return copy(scenes=[dict(headline='Invented claim', source_quote='this sentence is not in the article')] * 2) \
            if len(calls) == 1 else copy()
    assert write_reel(request(), provider=provider)['scenes'][0]['headline']
    assert len(calls) == 2
    with pytest.raises(ValueError, match='grounded'):
        write_reel(request(caption=True), provider=lambda r: copy(post_caption='The council approved 99 homes. ' * 3))


def test_writer_blocks_links_in_generated_text():
    bad = copy(post_caption='Read more at https://evil.example/now ' * 3)
    with pytest.raises(ValueError):
        write_reel(request(caption=True), provider=lambda r: bad)


# --- storage --------------------------------------------------------------

def test_drafts_use_revisions(creator, reel):
    payload = dict(reel=reel.model_dump(), caption='hi', source_text='', source_url='')
    saved = creator.store.save_draft('a' * 32, 0, payload)
    assert saved['revision'] == 1
    with pytest.raises(Conflict):
        creator.store.save_draft('a' * 32, 0, payload)
    assert creator.store.drafts()[0]['caption'] == 'hi'


def test_old_studio_data_is_dropped(tmp_path):
    root = tmp_path / 'creator'
    root.mkdir()
    db = sqlite3.connect(root / 'creator.sqlite3')
    db.executescript("CREATE TABLE drafts(id TEXT); INSERT INTO drafts VALUES('old');"
                     "CREATE TABLE jobs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);"
                     "INSERT INTO jobs VALUES('1','r1','f','{\"composition\": {}}');")
    db.commit(); db.close()
    from backend.services.reel_creator.store import Store
    store = Store(root)
    assert store.drafts() == [] and store.jobs() == []


# --- rendering ------------------------------------------------------------

def wait(creator, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = creator.store.job(job_id)
        if job['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            return job
        time.sleep(.2)
    raise AssertionError('render did not finish')


@pytest.mark.parametrize('kind', ['preview', 'final'])
def test_real_render_is_verified_h264_with_music(creator, reel, kind):
    from backend.services.reel_creator.reel import RenderInput
    creator.start()
    job = creator.enqueue(RenderInput(request_id='1' * 32, reel=reel, kind=kind, caption='caption'))
    job = wait(creator, job['id'])
    assert job['state'] == 'completed', job['error']
    path = creator.output_path(job)
    assert path.stat().st_size > 10_000
    assert job['post_caption'] == 'caption'


def test_render_cancel_and_idempotent_requests(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    first = creator.enqueue(RenderInput(request_id='2' * 32, reel=reel))
    again = creator.enqueue(RenderInput(request_id='2' * 32, reel=reel))
    assert first['id'] == again['id']
    with pytest.raises(Conflict):
        creator.enqueue(RenderInput(request_id='2' * 32, reel=reel.model_copy(update=dict(brand='Other'))))
    assert creator.store.cancel(first['id'])['state'] == 'cancelled'
    assert creator.retry(first['id'])['state'] == 'queued'


def test_pressured_render_waits_in_the_queue_and_resumes(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    reason = ['Waiting for automation or Live to finish']
    creator.admission = lambda: reason[0]
    job = creator.enqueue(RenderInput(request_id='4' * 32, reel=reel, kind='preview'))
    creator._render(creator.store.claim())
    job = creator.store.job(job['id'])
    assert job['state'] == 'queued' and job['reason'] == reason[0] and job['requeues'] == 1 and not job['error']
    reason[0] = None
    creator.start()
    assert wait(creator, job['id'])['state'] == 'completed'


def test_render_that_keeps_getting_pressured_stops(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    from backend.services.reel_creator.service import MAX_REQUEUES
    creator.admission = lambda: 'Waiting for automation or Live to finish'
    job = creator.enqueue(RenderInput(request_id='5' * 32, reel=reel, kind='preview'))
    creator.store.update(job['id'], requeues=MAX_REQUEUES)
    creator._render(creator.store.claim())
    job = creator.store.job(job['id'])
    assert job['state'] == 'interrupted' and 'Retry' in job['error']
    assert creator.retry(job['id'])['requeues'] == 0


def test_cancelled_while_pressured_is_not_requeued(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    job = creator.enqueue(RenderInput(request_id='6' * 32, reel=reel, kind='preview'))
    claimed = creator.store.claim()
    creator.store.cancel(job['id'])
    creator.admission = lambda: 'Waiting for available CPU'
    creator._render(claimed)
    assert creator.store.job(job['id'])['state'] == 'cancelled'


def test_scene_preview_is_available_while_a_video_renders(creator, reel):
    with creator.render_lock:  # held by the worker for a whole render
        assert creator.render_poster(reel, 1)[:2] == b'\xff\xd8'


def test_missing_asset_is_reported(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    reel.scenes[0].image_id = 'f' * 32
    with pytest.raises(ValueError, match='missing'):
        creator.enqueue(RenderInput(request_id='3' * 32, reel=reel))


def test_imports_normalize_images_and_find_focus(creator):
    asset = creator.import_asset(image_bytes((400, 300)), 'x.png', 'image')
    assert asset['focus']['x'] == .5 and asset['width'] == 400
    with pytest.raises(ValueError):
        creator.import_asset(b'not an image', 'x.png', 'image')
    with pytest.raises(ValueError):
        creator.import_asset(image_bytes(), 'x.png', 'audio')


# --- HTTP -----------------------------------------------------------------

@pytest.fixture
def client(creator, monkeypatch):
    monkeypatch.setattr(router, 'get_service', lambda: creator)
    app = FastAPI()
    app.include_router(router.router)
    return TestClient(app)


def test_api_poster_and_validation_errors(client, reel):
    response = client.post('/api/reel-creator/poster?scene=0', json=reel.model_dump())
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert Image.open(io.BytesIO(response.content)).size == (540, 960)
    assert client.post('/api/reel-creator/poster?scene=1', json=reel.model_dump()).status_code == 200
    reel.scenes[0].headline = 'W' * 110
    assert 'Scene 1' in client.post('/api/reel-creator/poster', json=reel.model_dump()).json()['detail']
    assert client.post('/api/reel-creator/assets/audio', files={'file': ('a.mp3', b'x')}).status_code == 400


def test_api_draft_roundtrip_and_conflict(client, reel):
    body = dict(revision=0, reel=reel.model_dump(), caption='c', goal='readmore', comment='Read it:\nhttps://example.com',
                source_text=ARTICLE, source_url='https://example.com/story', source_title='Flood plan', source_publisher='City Wire')
    assert client.put(f'/api/reel-creator/drafts/{"b" * 32}', json=body).json()['revision'] == 1
    assert client.put(f'/api/reel-creator/drafts/{"b" * 32}', json=body).status_code == 409
    saved = client.get('/api/reel-creator/drafts').json()[0]
    assert saved['reel']['look'] == 'breaking' and saved['goal'] == 'readmore' and saved['comment'].endswith('example.com')
    assert saved['source_title'] == 'Flood plan' and saved['source_publisher'] == 'City Wire'
    assert client.delete(f'/api/reel-creator/drafts/{"b" * 32}').status_code == 200
    assert client.get('/api/reel-creator/drafts').json() == []


# --- layouts and ratios ---------------------------------------------------

def rows_with_ink(frame, background):
    """Vertical extent of everything that differs from a flat background color."""
    width, height = frame.size
    pixels = frame.load()
    rows = [y for y in range(height) if any(pixels[x, y] != background for x in range(0, width, 3))]
    return rows[0], rows[-1]


def test_each_look_has_its_own_layout(creator, reel):
    """Same text, same photo: the compositions must differ in structure, not only color."""
    reel.scenes = reel.scenes[:1]
    images, logo, _ = creator.validate_assets(reel)
    plans = {}
    for look in LOOKS:
        reel.look = look
        plans[look] = Plan(reel, 360, images, logo)
    assert plans['politics'].rect[3] < plans['politics'].H * .6 and plans['politics'].page   # photo above a solid panel
    assert plans['clean'].rect[0] > 0 and plans['clean'].mask is not None            # inset rounded card
    assert plans['breaking'].rect[:4] == (0, 0, 360, 640) == plans['hot'].rect[:4]  # full bleed
    assert any(e.anim == 'marquee' for e in plans['breaking'].global_elements)       # ticker
    assert not any(e.anim == 'marquee' for p in ('hot', 'politics', 'clean') for e in plans[p].global_elements)
    ys = {look: min(e.y for e in plans[look].scene_elements[0] if e.tile.width > 40) for look in LOOKS}
    assert ys['hot'] < ys['breaking'] and ys['hot'] < ys['clean']                      # hot puts its headline up top
    frames = {look: plans[look].frame(plans[look].settled(0)).tobytes() for look in LOOKS}
    assert len(set(frames.values())) == len(LOOKS) == 7
    editorial = plans['editorial']
    assert editorial.page and editorial.rect[1] > editorial.H * .4 and editorial.rect[3] < editorial.H * .6   # photo across the bottom
    text_y = min(e.y for e in editorial.scene_elements[0])
    assert text_y < editorial.rect[1]                                                                         # headline above the photo
    assert plans['wire'].rect[:4] == (0, 0, 360, 640)                                                         # photo under a kicker block


@pytest.mark.parametrize('look', list(LOOKS))
def test_layout_adapts_to_ratio_and_keeps_text_inside_the_safe_area(look, creator, reel):
    reel.look = look
    images, logo, _ = creator.validate_assets(reel)
    for ratio, bottom_margin in (('9:16', 380), ('4:5', 100)):
        reel.ratio = ratio
        plan = Plan(reel, 360, images, logo)
        limit = plan.px(plan.DH - bottom_margin)
        for element in plan.scene_elements[0]:
            assert element.y + element.tile.height <= limit + plan.px(30), (look, ratio)  # glyph tails may dip a little
    reel.ratio = '9:16'
    portrait = Plan(reel, 360, images, logo)
    reel.ratio = '4:5'
    feed = Plan(reel, 360, images, logo)
    assert feed.H < portrait.H and feed.W == portrait.W


@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
@pytest.mark.parametrize('look', list(LOOKS))
def test_longest_allowed_text_fits_every_look_and_ratio(look, ratio):
    value = Reel(look=look, ratio=ratio, tag='BREAKING NEWS', source='A long publisher name here', brand='Brand',
                 scenes=[Scene(headline=('Headline words ' * 8)[:120].strip(), detail=('detail words ' * 13)[:160].strip())])
    Plan(value, 360, {}, None)


@pytest.mark.parametrize('look', list(LOOKS))
def test_reels_text_keeps_clear_of_the_button_column(look, creator, reel):
    """On 9:16 the like, comment and share buttons cover the right edge; text panels may run under them, text may not."""
    reel.look = look
    images, logo, _ = creator.validate_assets(reel)
    for ratio, right in (('9:16', 150), ('4:5', 64)):
        reel.ratio = ratio
        plan = Plan(reel, 1080, images, logo)
        for element in plan.scene_elements[0]:
            if element.tile.width < plan.W * .95:          # full-width straps are backgrounds, not text
                assert element.x + element.tile.width <= plan.W - right + 10, (look, ratio)


def test_each_look_sets_its_own_display_face():
    from backend.services.reel_creator.renderer import TYPE
    from backend.services.reel_creator.typeset import FACES, FONTS
    assert {TYPE[look]['head'][0] for look in LOOKS} >= {'display', 'condensed', 'serif', 'bold'}
    assert all((FONTS / name).is_file() for name in FACES.values())
    for look in LOOKS:
        assert TYPE[look]['detail'][2] >= 34                # the smallest detail text stays readable on a phone


def test_display_face_hands_missing_characters_to_noto():
    from backend.services.reel_creator.typeset import resolve
    assert resolve('Council vote', 'display') == 'display'
    assert resolve('λ council', 'display') == 'bold' and resolve('Ŧ', 'serif-italic') == 'regular'
    Plan(Reel(look='hot', scenes=[Scene(headline='λ is drawn in Noto')]), 360, {}, None).frame(1)


@pytest.mark.parametrize('look', ['politics', 'clean', 'editorial'])
def test_photo_takes_the_room_short_text_leaves(look):
    value = Reel(look=look, scenes=[Scene(headline='Vote passes', backdrop='#334455'),   # a color keeps the photo frame
                                   Scene(headline='Council approves a flood relief plan for three provinces after a long debate',
                                         detail='Payments begin next week after the cabinet signs the order, officials said.', backdrop='#334455')])
    plan = Plan(value, 1080, {}, None)
    short, long = plan.rects
    assert short[3] > long[3]                                # a bigger photo for the short scene
    assert plan.rect == short                                # the opening card's frame


def test_blur_fill_shows_the_whole_photo(creator):
    wide = io.BytesIO()
    photo = Image.new('RGB', (1600, 900), (40, 40, 40))
    photo.paste((255, 0, 0), (0, 0, 120, 900))               # red left edge
    photo.paste((0, 0, 255), (1480, 0, 1600, 900))           # blue right edge
    photo.save(wide, 'PNG')
    asset = creator.import_asset(wide.getvalue(), 'wide.png', 'image')
    images = {asset['id']: creator.asset_path(asset['id'], 'image')}

    def edges(fit):
        value = Reel(look='breaking', motion='none', scenes=[Scene(headline='Vote', image_id=asset['id'], fit=fit)])
        frame = Plan(value, 360, images, None).background(0, 1)
        colors = {frame.getpixel((x, y)) for x in range(frame.width) for y in range(0, frame.height, 8)}
        return any(r > 200 and g < 60 and b < 60 for r, g, b in colors), any(b > 200 and r < 60 and g < 60 for r, g, b in colors)
    assert edges('cover') == (False, False)                  # a 16:9 photo loses both sides at 9:16
    assert edges('blur') == (True, True)                     # blur fill keeps the whole width
    with pytest.raises(ValidationError):
        Scene(fit='stretch')


def test_feed_ratio_renders_a_verified_4_5_video(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    reel.ratio = '4:5'
    creator.start()
    job = creator.enqueue(RenderInput(request_id='4' * 32, reel=reel, kind='preview'))
    job = wait(creator, job['id'])
    assert job['state'] == 'completed', job['error']
    probe = __import__('subprocess').run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=width,height',
                                          '-of', 'csv=p=0', str(creator.output_path(job))], capture_output=True, text=True)
    assert probe.stdout.strip() == '540,676'


# --- music ----------------------------------------------------------------

def wav_bytes(seconds=1):
    import wave
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
        out.writeframes(b'\x00\x01' * 8000 * seconds)
    return data.getvalue()


def test_music_options_resolve_to_tracks(creator, reel):
    from backend.services.reel_creator.audio_presets import PRESETS
    assert Reel(look='politics').music_preset == LOOKS['politics']['music']            # auto follows the look
    assert Reel(music='off').music_preset is None
    assert Reel(music=next(iter(PRESETS))).music_preset == next(iter(PRESETS))
    assert creator.validate_assets(reel.model_copy(update=dict(music='off')))[2] is None
    assert creator.validate_assets(reel)[2].suffix == '.wav'


def test_uploaded_music_is_checked_then_used(creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    asset = creator.import_asset(wav_bytes(), 'theme.wav', 'audio')
    assert asset['kind'] == 'audio' and asset['duration'] == 1
    with pytest.raises(ValueError):
        creator.import_asset(b'#EXTM3U\nhttp://evil.example/x', 'list.m3u', 'audio')
    with pytest.raises(ValueError, match='no audio|Choose'):
        creator.import_asset(image_bytes(), 'cover.png', 'audio')
    reel.music, reel.music_id = 'custom', asset['id']
    assert creator.validate_assets(reel)[2].name == asset['filename']
    creator.start()
    job = wait(creator, creator.enqueue(RenderInput(request_id='5' * 32, reel=reel, kind='preview'))['id'])
    assert job['state'] == 'completed', job['error']  # a 1 second clip loops under the reel


def test_music_endpoints(client):
    tracks = client.get('/api/reel-creator/capabilities').json()['music']
    assert len(tracks) == 9 and {'id', 'name', 'description', 'duration'} <= set(tracks[0])
    assert client.get(f'/api/reel-creator/music/{tracks[0]["id"]}/preview').headers['content-type'] == 'audio/wav'
    assert client.get('/api/reel-creator/music/unknown/preview').status_code == 400
    assert client.get('/api/reel-creator/capabilities').json()['ratios'] == ['9:16', '4:5']


# --- scene count follows photos -------------------------------------------

def test_writer_writes_one_scene_per_photo():
    def provider(r):
        return copy(scenes=[dict(headline=['Vote passes', 'Plan covers provinces', 'Payments soon', 'Cabinet signs order', 'Relief for floods'][i], source_quote='voted 7 to 2 on Tuesday to approve a new flood relief plan')
                            for i in range(5)])
    assert len(write_reel(request(scenes=4), provider=provider)['scenes']) == 4
    assert len(write_reel(request(scenes=1), provider=provider)['scenes']) == 1
    assert len(write_reel(request(), provider=lambda r: copy())['scenes']) == 2     # no photos: the AI decides
    with pytest.raises(ValueError, match='grounded'):
        write_reel(request(scenes=6), provider=provider)                           # model returned too few


def test_writer_adds_scenes_that_continue_an_existing_reel():
    seen = []

    def provider(r):
        seen.append(r.provider_context())
        return copy(scenes=[dict(headline='Payments start next week', source_quote='payments will begin next week')])
    result = write_reel(request(scenes=1, existing=['Council backs flood relief plan']), provider=provider)
    assert [s['headline'] for s in result['scenes']] == ['Payments start next week']
    assert seen[0]['existing_headlines'] == ['Council backs flood relief plan'] and seen[0]['scene_count'] == 1


# --- politics look, motion, cover image, brand line -------------------------

def test_old_story_drafts_open_as_politics():
    assert Reel.model_validate({'look': 'story'}).look == 'politics'
    assert LOOKS['politics']['tag'] == 'POLITICS' and 'story' not in LOOKS


def test_photo_zoom_follows_the_motion_setting(creator, reel):
    reel.scenes = reel.scenes[:1]
    images, logo, _ = creator.validate_assets(reel)
    first_vs_last = {}
    for motion in ('zoom_in', 'zoom_out', 'none', 'auto'):
        reel.motion = motion
        plan = Plan(reel, 360, images, logo)
        # A photo with a hard edge shows scale: compare early and late background frames.
        early, late = plan.background(0, 0), plan.background(0, plan.lengths[0])
        first_vs_last[motion] = early.tobytes() != late.tobytes()
    assert first_vs_last == {'zoom_in': True, 'zoom_out': True, 'none': False, 'auto': True}
    zoom_in = Plan(reel.model_copy(update=dict(motion='zoom_in')), 360, images, logo)   # a Plan reads its reel live
    zoom_out = Plan(reel.model_copy(update=dict(motion='zoom_out')), 360, images, logo)
    assert zoom_in.background(0, 0).tobytes() == zoom_out.background(0, zoom_out.lengths[0]).tobytes()


def test_brand_is_one_wide_line_and_clears_the_live_badge(creator, reel):
    reel.look, reel.brand = 'breaking', 'A very long news organisation name that keeps going well past the edge of the screen'
    images, logo, _ = creator.validate_assets(reel)
    unbadged = Plan(reel, 1080, images, logo)
    assert not any(e.anim == 'blink' for e in unbadged.global_elements)   # LIVE is only for coverage marked live
    assert any(e.anim == 'marquee' for e in unbadged.global_elements)     # the ticker stays
    reel.live = True
    plan = Plan(reel, 1080, images, logo)
    brand = next(e for e in plan.global_elements if e.anim == 'rise')
    badge = next(e for e in plan.global_elements if e.anim == 'pop' and e.tile.width < 400 and e.x > 500)
    assert brand.x + brand.tile.width <= badge.x                       # never under the badge
    wide = next(e for e in unbadged.global_elements if e.anim == 'rise')
    assert wide.tile.width > brand.tile.width                            # without the badge the brand gets its room
    reel.look, reel.brand = 'clean', 'Short name'
    plain = Plan(reel, 1080, images, logo)
    short = next(e for e in plain.global_elements if e.anim == 'rise')
    assert short.tile.height > brand.tile.height * .95                  # short names get the larger type
    assert short.tile.width < 700


def test_finished_video_has_a_cover_from_the_complete_first_frame(creator, reel):
    import subprocess
    from backend.services.reel_creator.reel import RenderInput
    from backend.services.reel_creator.service import tool
    creator.start()
    job = wait(creator, creator.enqueue(RenderInput(request_id='6' * 32, reel=reel, kind='final'))['id'])
    assert job['state'] == 'completed', job['error']
    cover = Image.open(creator.job_thumbnail_path(job))
    assert cover.size == (1080, 1920) and job['output']['thumbnail'].endswith('.jpg')
    plan = Plan(reel, 1080, *creator.validate_assets(reel)[:2], fps=30)
    expected = plan.frame(0).convert('RGB')
    diff = sum(abs(a - b) for a, b in zip(cover.convert('RGB').resize((54, 96)).tobytes(), expected.resize((54, 96)).tobytes()))
    assert diff / (54 * 96 * 3) < 3                                      # same picture, JPEG noise aside
    decoded = subprocess.run([tool('ffmpeg'), '-v', 'error', '-i', str(creator.output_path(job)),
                              '-frames:v', '1', '-threads', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'],
                             capture_output=True, check=True, timeout=30)
    first = Image.open(io.BytesIO(decoded.stdout)).convert('RGB').resize((54, 96))
    diff = sum(abs(a - b) for a, b in zip(first.tobytes(), expected.resize((54, 96)).tobytes()))
    assert diff / (54 * 96 * 3) < 5  # the encoded video starts with the same readable card


def test_cover_endpoint(client, creator, reel):
    from backend.services.reel_creator.reel import RenderInput
    creator.start()
    job = wait(creator, creator.enqueue(RenderInput(request_id='7' * 32, reel=reel, kind='preview'))['id'])
    assert client.get(f'/api/reel-creator/jobs/{job["id"]}/thumbnail').headers['content-type'] == 'image/jpeg'
    assert client.get(f'/api/reel-creator/jobs/{"0" * 32}/thumbnail').status_code == 404


def test_wire_and_editorial_show_source_and_date_but_other_looks_stay_quiet(creator, reel):
    reel.scenes = reel.scenes[:1]
    reel.dateline, reel.source = 'Oct 25, 2026', 'City Wire'
    images, logo, _ = creator.validate_assets(reel)

    def line_count(look):
        return len(Plan(reel.model_copy(update=dict(look=look)), 360, images, logo).scene_elements[0])
    # wire: rule + kicker + credit + headline lines + detail lines; without a date the credit is the source alone
    with_date = line_count('wire')
    reel.dateline = ''
    assert line_count('wire') == with_date                  # the credit line remains (source), date just leaves it
    reel.source = ''
    assert line_count('wire') == with_date - 1              # no source and no date: no credit line


def test_editorial_deck_is_serif_and_falls_back_for_glyphs_the_serif_lacks(creator):
    from backend.services.reel_creator.typeset import serif_ok
    assert serif_ok('Officials say payments begin next week.') and not serif_ok('Ŧ')
    value = Reel(look='editorial', scenes=[Scene(headline='Headline', detail='Ŧ is not in the serif face')])
    Plan(value, 360, {}, None).frame(2)                      # the deck renders in the sans face rather than failing


def test_unsupported_scripts_are_reported_not_drawn_as_boxes():
    with pytest.raises(ValueError, match='cannot display'):
        Plan(Reel(scenes=[Scene(headline='ព័ត៌មាន')]), 360, {}, None)


# --- goals: engage vs read more -----------------------------------------------

def test_goal_reaches_the_ai_and_defaults_to_engage():
    seen = []

    def provider(r):
        seen.append(r.provider_context())
        return copy()
    write_reel(request(), provider=provider)
    write_reel(request(goal='readmore'), provider=provider)
    assert [c['goal'] for c in seen] == ['engage', 'readmore']
    from backend.services.reel_creator.writer import SYSTEM
    assert 'engage:' in SYSTEM and 'readmore:' in SYSTEM and 'engagement\n  bait' in SYSTEM


def test_read_more_puts_the_link_in_a_first_comment_never_in_the_caption():
    lead = dict(comment_lead='Read what officials said about who qualifies:')
    result = write_reel(request(goal='readmore', caption=True), provider=lambda r: copy(**lead))
    assert result['comment'] == 'Read what officials said about who qualifies:\nhttps://example.com/story'
    assert 'http' not in result['caption'] and 'first comment' in result['caption'].lower()
    # A caption that already points to the comment is left alone.
    pointed = write_reel(request(goal='readmore', caption=True), provider=lambda r: copy(post_caption='Flood relief is coming. Details are in the comments.'))
    assert pointed['caption'].lower().count('comment') == 1
    # Pasted text has no link: the comment still leads the reader, and the user adds the link.
    no_url = request(goal='readmore', caption=True)
    no_url.source_url = ''
    assert write_reel(no_url, provider=lambda r: copy(**lead))['comment'] == lead['comment_lead']
    assert write_reel(no_url, provider=lambda r: copy())['comment'] == 'Read the full story:'


def test_engage_captions_do_not_link_or_send_readers_elsewhere():
    result = write_reel(request(goal='engage', caption=True), provider=lambda r: copy(comment_lead='ignored'))
    assert result['comment'] == '' and 'first comment' not in result['caption'].lower()


def test_comment_lead_cannot_smuggle_a_link():
    with pytest.raises(ValueError):
        write_reel(request(goal='readmore', caption=True), provider=lambda r: copy(comment_lead='More at https://evil.example'))


def test_no_comment_without_a_caption_request():
    assert write_reel(request(goal='readmore'), provider=lambda r: copy(comment_lead='Read more:'))['comment'] == ''


# --- scene kinds: stat, quote, end card, highlights ---------------------------

QUOTED = ARTICLE + ' "Families cannot wait another month for help," said Mayor Lina Chhay.'


def quoted_request(**extra):
    return WriteRequest(api_key='k', model='gemini-test', text=QUOTED, publisher='City Wire', **extra)


def test_writer_keeps_grounded_stats_quotes_and_end_card():
    scenes = [dict(headline='Council backs flood relief plan', source_quote='voted 7 to 2 on Tuesday', highlight='flood relief'),
              dict(kind='stat', stat='7 to 2', headline='Council vote on the plan', source_quote='voted 7 to 2 on Tuesday'),
              dict(kind='quote', headline='“Families cannot wait another month for help”', attribution='Mayor Lina Chhay',
                   source_quote='Families cannot wait another month for help', highlight='another month')]
    end = dict(headline='Should relief come before repairs?', highlight='before repairs')
    result = write_reel(quoted_request(), provider=lambda r: copy(scenes=scenes, end_card=end))
    kinds = [(s['kind'], s['stat'], s['attribution']) for s in result['scenes']]
    assert kinds == [('story', '', ''), ('stat', '7 to 2', ''), ('quote', '', 'Mayor Lina Chhay')]
    assert result['scenes'][2]['headline'] == 'Families cannot wait another month for help'   # marks stripped, words verbatim
    assert result['scenes'][0]['highlight'] == 'flood relief' and result['scenes'][2]['highlight'] == 'another month'
    assert result['end'] == dict(headline='Should relief come before repairs?', detail='', highlight='before repairs')


def test_writer_turns_unsupported_stats_and_quotes_into_stories():
    scenes = [dict(kind='stat', stat='7 to 2', headline='Hook stays a story', source_quote='voted 7 to 2 on Tuesday'),
              dict(kind='quote', headline='Families need help now', attribution='Mayor Lina Chhay', source_quote='voted 7 to 2 on Tuesday'),
              dict(kind='quote', headline='Families cannot wait another month for help', attribution='Governor Sok Dara',
                   source_quote='voted 7 to 2 on Tuesday'),
              dict(kind='stat', stat='many', headline='Many provinces covered', source_quote='covering three provinces',
                   highlight='nowhere in headline')]
    result = write_reel(quoted_request(), provider=lambda r: copy(scenes=scenes))
    assert [s['kind'] for s in result['scenes']] == ['story'] * 4         # hook, paraphrase, unnamed speaker, no figure
    assert all(not s['stat'] and not s['attribution'] for s in result['scenes'])
    assert result['scenes'][3]['highlight'] == '' and result['end'] is None
    whole = write_reel(quoted_request(), provider=lambda r: copy(scenes=[dict(headline='Council backs plan', highlight='Council backs plan',
                                                                              source_quote='voted 7 to 2 on Tuesday')]))
    assert whole['scenes'][0]['highlight'] == ''                           # never the whole headline


def test_writer_rejects_invented_figures_and_links_in_new_fields():
    stat = [dict(headline='Council backs plan', source_quote='voted 7 to 2 on Tuesday'),
            dict(kind='stat', stat='99%', headline='Support for the plan', source_quote='voted 7 to 2 on Tuesday')]
    with pytest.raises(ValueError, match='grounded'):
        write_reel(request(), provider=lambda r: copy(scenes=stat))
    with pytest.raises(ValueError, match='grounded'):
        write_reel(request(), provider=lambda r: copy(end_card=dict(headline='See www.example.com for more')))


def test_writer_adds_no_end_card_when_extending_a_reel():
    result = write_reel(request(scenes=1, existing=['Council backs flood relief plan']),
                        provider=lambda r: copy(scenes=[dict(headline='Payments start next week', source_quote='payments will begin next week')],
                                                end_card=dict(headline='What do you think?')))
    assert result['end'] is None


def test_scene_kinds_validate_and_count_toward_reading_time():
    with pytest.raises(ValidationError):
        Scene(kind='poll')
    assert Scene(kind='quote', headline='a b', attribution='c d e').length() > Scene(headline='a b').length()
    assert Scene(kind='quote', headline='Words', attribution='Mayor').detail_text == '— Mayor'
    assert Scene(kind='stat', stat='7-2').lead == '7-2' and Scene(kind='story', stat='7-2').lead == ''
    assert Scene(kind='end', headline='Agree?').lead == '?' and Scene(kind='end', headline='Full story below').lead == '→'


def test_highlight_spans_whole_words_across_wrapped_lines():
    from backend.services.reel_creator.reel import highlight_span
    assert highlight_span('Council approves  flood relief', 'FLOOD relief') == (17, 29)
    assert highlight_span('The vote was 72 to 2', '2') == (19, 20)                  # not the 2 inside 72
    assert highlight_span('Council approves', 'approve') is None
    plan = Plan(Reel(look='clean', scenes=[Scene(headline='Council approves flood relief for three provinces',
                                                 highlight='flood relief for three')]), 360, {}, None)
    plan._scene = plan.reel.scenes[0]
    marked = plan._highlights(['Council approves flood', 'relief for three', 'provinces'])
    assert marked == [(17, 22), (0, 16), None]


@pytest.mark.parametrize('look', list(LOOKS))
def test_stat_quote_and_end_scenes_render_in_every_look(look):
    value = Reel(look=look, scenes=[
        Scene(headline='Council approves flood relief', highlight='flood relief'),
        Scene(kind='stat', stat='7-2', headline='Council vote on the plan'),
        Scene(kind='quote', headline='Families cannot wait another month', attribution='Mayor Lina Chhay'),
        Scene(kind='end', headline='Should relief come before repairs?')])
    plan = Plan(value, 360, {}, None)
    for index in range(4):
        plan.frame(plan.settled(index))
    elements = plan.scene_elements[1]
    figure = max((e for e in elements if e.anim == 'pop'), key=lambda e: e.tile.height)
    lines = [e for e in elements if e.anim == 'rise' and plan.px(20) < e.tile.width < plan.W * .95]   # headline lines, not straps
    assert figure.tile.height > max(e.tile.height for e in lines)               # the figure is the biggest type on the card
    highlighted = Plan(value, 360, {}, None).frame(plan.settled(0)).tobytes()
    plain = Plan(value.model_copy(update=dict(scenes=[value.scenes[0].model_copy(update=dict(highlight=''))])), 360, {}, None)
    assert plain.frame(plain.settled(0)).tobytes() != highlighted


def test_highlight_missing_from_the_headline_is_ignored():
    with_missing = Plan(Reel(scenes=[Scene(headline='Council approves plan', highlight='not there')]), 360, {}, None)
    without = Plan(Reel(scenes=[Scene(headline='Council approves plan')]), 360, {}, None)
    assert with_missing.frame(1).tobytes() == without.frame(1).tobytes()


def test_an_end_card_without_a_photo_is_the_page_itself():
    plan = Plan(Reel(look='clean', scenes=[Scene(headline='Vote passes', backdrop='#334455'), Scene(kind='end', headline='Agree?')]), 360, {}, None)
    assert plan.rects[1][3] == 0 and plan.rects[0][3] > 0
    assert plan.background(1, 1).getpixel((180, 300)) == plan.page


def test_a_figure_too_wide_for_the_card_is_reported():
    with pytest.raises(ValueError, match='figure is too wide'):
        Plan(Reel(scenes=[Scene(kind='stat', stat='W' * 24, headline='Label')]), 360, {}, None)


def test_gemini_is_asked_for_scene_kinds_highlights_and_the_end_card():
    """Gemini skips optional schema fields; these must be required in what it receives, while the parser keeps defaults."""
    from backend.services.reel_creator.writer import ReelCopy
    schema = ReelCopy.model_json_schema()
    assert 'end_card' in schema['required']
    assert {'kind', 'stat', 'attribution', 'highlight'} <= set(schema['$defs']['SceneCopy']['required'])
    assert ReelCopy.model_validate(dict(scenes=[dict(headline='Vote passes', source_quote='voted 7 to 2 on Tuesday')])).end_card is None


@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
def test_pop_art_preserves_cutout_and_draws_halftone_halo_outline_and_no_byline(creator, ratio):
    from PIL import ImageDraw
    photo = Image.new('RGBA', (400, 600))
    ImageDraw.Draw(photo).rectangle((100, 50, 300, 599), fill=(230, 60, 40, 255))
    data = io.BytesIO()
    photo.save(data, 'PNG')
    asset = creator.import_asset(data.getvalue(), 'portrait.png', 'image')
    path = creator.asset_path(asset['id'], 'image')
    with Image.open(path) as saved:
        assert saved.mode == 'RGBA' and saved.getpixel((0, 0))[3] == 0
    value = Reel(look='pop_art', ratio=ratio, motion='none', show_progress=False, brand='Daily News',
                 source='Must not appear as a footer', scenes=[Scene(headline='Technology changes the workplace', image_id=asset['id'])])
    plan = Plan(value, 540, {asset['id']: path})
    background = plan._base(0)
    colors = set(background.getdata())
    assert {(17, 17, 17), (204, 255, 0), (255, 255, 255)} <= colors    # halftone page, accent halo, white outline
    # The portrait keeps its own color, while its transparent surroundings show the design.
    assert background.getpixel((background.width // 2, round(background.height * .7))) == (230, 60, 40)
    assert [e.role for e in plan.global_elements] == ['brand']           # the centered brand pill only
    assert any(e.role == 'label' for e in plan.scene_elements[0])        # the label rides the seam
    frame = plan.frame(plan.settled(0))
    assert frame.getpixel((20, frame.height - 20)) == (17, 17, 17)
    assert plan.rect[3] < plan.H * .65


def test_writer_accepts_explicit_pop_art_look():
    assert WriteRequest(api_key='test', model='gemini-test', text=ARTICLE, look='pop_art').look == 'pop_art'


# --- advanced mode --------------------------------------------------------

def styled(reel, style=None, scenes=None):
    """The reel validated again with advanced settings: per-scene dicts are merged into the scenes by index."""
    value = reel.model_dump()
    if style is not None:
        value['style'] = style
    for index, changes in (scenes or {}).items():
        value['scenes'][index].update(changes)
    return Reel.model_validate(value)


def test_styles_validate_and_merge_scene_over_reel():
    from backend.services.reel_creator.reel import ElementStyle
    value = Reel(style={'headline': {'color': '#FFEE00', 'font': 'serif'}},
                 scenes=[Scene(headline='A', style={'headline': {'color': '#222222', 'hidden': False}})])
    merged = value.scenes[0].style_for(value.style, 'headline')
    assert (merged.color, merged.font, merged.hidden) == ('#222222', 'serif', False)
    assert value.style['headline'].color == '#ffee00'
    assert value.scenes[0].style_for(value.style, 'detail') is None
    for bad in ({'scale': 2}, {'color': 'red'}, {'font': 'comic'}, {'animation': 'marquee'}, {'shadow': True}):
        with pytest.raises(ValidationError):
            Reel(style={'headline': bad})
    with pytest.raises(ValidationError):
        Reel(style={'footer': ElementStyle()})
    with pytest.raises(ValidationError):
        Scene(transition='spin')


@pytest.mark.parametrize('look', list(LOOKS))
def test_empty_styles_draw_exactly_the_simple_reel(look, creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    reel.look = look
    plain = Plan(reel, 360, images, logo)
    styled = Plan(reel.model_copy(update=dict(style={'headline': {}}, scenes=[s.model_copy(update=dict(style={'detail': {}})) for s in reel.scenes])), 360, images, logo)
    for t in (.5, 1.5, 3.5):
        assert plain.frame(t).tobytes() == styled.frame(t).tobytes(), (look, t)


@pytest.mark.parametrize('look', list(LOOKS))
def test_every_look_tags_its_text_by_role(look, creator, reel):
    reel.look = look
    reel.scenes[1] = Scene(kind='stat', stat='7-2', headline='Council vote', detail='Two members against.')
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 360, images, logo)
    assert {'headline', 'detail'} <= {e.role for e in plan.scene_elements[0]}
    assert 'lead' in {e.role for e in plan.scene_elements[1]}
    roles = {e.role for e in plan.global_elements}
    assert 'brand' in roles
    if look == 'breaking':
        assert 'ticker' in roles


def test_hidden_roles_are_not_drawn_and_give_their_room_back(creator, reel):
    reel.look = 'politics'
    images, logo, _ = creator.validate_assets(reel)
    plain = Plan(reel, 1080, images, logo)
    hidden = Plan(styled(reel, {'brand': {'hidden': True}, 'label': {'hidden': True}}, {0: {'style': {'detail': {'hidden': True}}}}), 1080, images, logo)
    assert not {'detail', 'label'} & {e.role for e in hidden.scene_elements[0]}
    assert 'brand' not in {e.role for e in hidden.global_elements}
    assert hidden.rects[0][3] > plain.rects[0][3]              # the photo grows into the detail's room


def test_scene_shows_what_the_reel_hides(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(styled(reel, {'headline': {'hidden': True}}, {1: {'style': {'headline': {'hidden': False}}}}), 360, images, logo)
    assert not any(e.role == 'headline' for e in plan.scene_elements[0])
    assert any(e.role == 'headline' for e in plan.scene_elements[1])


def test_font_color_size_and_line_box_change_the_headline(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    reel.look = 'wire'
    plain = Plan(reel, 1080, images, logo)
    boxed = Plan(styled(reel, {'headline': {'font': 'serif', 'scale': .8, 'color': '#ffee00', 'background': '#1e3a8a'}}), 1080, images, logo)
    assert boxed.type['head'] == plain.type['head']              # the identity row is back on the look's type
    first = lambda plan: next(e for e in plan.scene_elements[0] if e.role == 'headline')
    line = first(boxed)
    pixels = {line.tile.getpixel((x, line.tile.height // 2))[:3] for x in range(0, line.tile.width, 4)}
    assert (30, 58, 138) in pixels and (255, 238, 0) in pixels   # the box, and the text drawn over it
    assert line.x < first(plain).x                                 # the box reaches left; the letters stay on the margin


def test_entrance_override_keeps_the_look_timing_and_the_ticker_moving(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    plain = Plan(reel, 360, images, logo)
    plan = Plan(styled(reel, {'headline': {'animation': 'none'}, 'ticker': {'background': '#111111'}}), 360, images, logo)
    lines = [e for e in plan.scene_elements[1] if e.role == 'headline']
    look = [e for e in plain.scene_elements[1] if e.role == 'headline']
    assert lines and all(e.anim == 'none' for e in lines)
    assert [(e.start, e.dur) for e in lines] == [(e.start, e.dur) for e in look]
    assert next(e for e in plan.global_elements if e.role == 'ticker').anim == 'marquee'


def test_retired_entrance_timing_in_older_drafts_is_ignored():
    value = Reel(style={'headline': {'delay': 2.5, 'duration': 1, 'color': '#ffee00'}})
    assert value.model_dump()['style'] == {'headline': {'color': '#ffee00'}}


def test_scene_transition_motion_shade_and_backdrop(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    reel.scenes[1] = reel.scenes[1].model_copy(update=dict(transition='cut', backdrop='#7c2d12', shade=0, motion='none'))
    plan = Plan(reel, 360, images, logo)
    before_cut = plan.starts[1] - .05
    cut = plan._timeline_frame(before_cut)
    fade = Plan(reel.model_copy(update=dict(scenes=[reel.scenes[0], reel.scenes[1].model_copy(update=dict(transition=None))])), 360, images, logo)._timeline_frame(before_cut)
    assert cut.tobytes() != fade.tobytes()
    still = plan.background(1, 0), plan.background(1, 1.9)
    assert still[0].tobytes() == still[1].tobytes()                # motion: still
    assert still[0].getpixel((plan.W // 2, plan.H // 2)) == (124, 45, 18)  # backdrop, with no shade over it
    slide = Plan(reel.model_copy(update=dict(scenes=[reel.scenes[0], reel.scenes[1].model_copy(update=dict(transition='slide'))])), 360, images, logo)
    frame = slide._timeline_frame(plan.starts[1] - .1)
    assert frame.getpixel((plan.W - 2, plan.H // 2)) == (124, 45, 18) and frame.getpixel((2, plan.H // 2)) != (124, 45, 18)


def test_styled_draft_and_poster_roundtrip(client, reel):
    body = reel.model_dump()
    body['style'] = {'headline': {'color': '#ffee00'}}
    body['scenes'][0]['style'] = {'detail': {'hidden': True}}
    body['scenes'][1]['transition'] = 'slide'
    saved = client.put('/api/reel-creator/drafts/' + 'a' * 32, json=dict(revision=0, reel=body))
    assert saved.status_code == 200, saved.text
    assert saved.json()['reel']['style'] == {'headline': {'color': '#ffee00'}}   # unset fields are left out, not null
    assert saved.json()['reel']['scenes'][0]['style'] == {'detail': {'hidden': True}}
    assert client.post('/api/reel-creator/poster?scene=1', json=body).status_code == 200


@pytest.mark.parametrize('look', list(LOOKS))
def test_line_boxes_keep_clear_of_the_button_column(look, creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    boxes = {'headline': {'background': '#1e3a8a'}, 'detail': {'background': '#000000'}}
    for ratio, right in (('9:16', 150), ('4:5', 64)):
        plan = Plan(styled(reel, boxes, {0: {}}).model_copy(update=dict(look=look, ratio=ratio)), 1080, images, logo)
        for element in plan.scene_elements[0]:
            if element.role in ('headline', 'detail') and element.tile.width < plan.W * .95:
                assert element.x + element.tile.width <= plan.W - right + 10, (look, ratio)


def test_wire_credit_takes_the_label_color(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(styled(reel, {'label': {'color': '#00ff00'}}).model_copy(update=dict(look='wire')), 360, images, logo)
    labels = [e for e in plan.scene_elements[0] if e.role == 'label']
    assert len(labels) == 2
    for element in labels:                       # the tag and the source line
        assert any(element.tile.getpixel((x, y))[:3] == (0, 255, 0)
                   for x in range(element.tile.width) for y in range(element.tile.height))


# --- advanced mode: preview selection and text position --------------------

@pytest.mark.parametrize('look', list(LOOKS))
def test_layout_boxes_cover_what_the_preview_draws(look, creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    reel.look = look
    plan = Plan(reel, 540, images, logo)
    layout = plan.layout(0)
    boxes = {box['role']: box for box in layout['boxes']}
    assert {'headline', 'detail'} <= set(boxes)
    for box in boxes.values():
        assert 0 <= box['x'] and 0 <= box['y'] and box['x'] + box['w'] <= 1.0001 and box['y'] + box['h'] <= 1.0001
    for element in plan.scene_elements[0]:
        if element.role == 'headline':
            b = boxes['headline']
            assert b['x'] * plan.W - 1 <= element.x and element.y >= b['y'] * plan.H - 1
    areas = [box['w'] * box['h'] for box in layout['boxes']]
    assert areas == sorted(areas, reverse=True)          # smaller boxes last, so they sit on top
    assert (layout['position'] is not None) == (look in ('breaking', 'wire'))


def test_layout_leaves_out_hidden_elements(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(styled(reel, {'brand': {'hidden': True}}), 540, images, logo)
    roles = {box['role'] for box in plan.layout(1)['boxes']}
    assert 'brand' not in roles and 'headline' in roles


@pytest.mark.parametrize('look', ['breaking', 'wire'])
@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
def test_text_block_moves_within_the_safe_area(look, ratio, creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    base = reel.model_copy(update=dict(look=look, ratio=ratio))
    plain = Plan(base, 1080, images, logo)
    assert plain.positions[0] == 1.0                     # both looks sit their text at the bottom
    span = lambda plan: (min(e.y for e in plan.scene_elements[0]), max(e.y + e.tile.height for e in plan.scene_elements[0]))
    high = Plan(styled(base, scenes={0: {'text_position': 0}}), 1080, images, logo)
    middle = Plan(styled(base, scenes={0: {'text_position': .5}}), 1080, images, logo)
    assert span(high)[0] == plain.px(plain.inset + 120)          # just under the logo row
    assert span(high)[0] < span(middle)[0] < span(plain)[0]
    assert span(high)[1] - span(high)[0] == span(plain)[1] - span(plain)[0]   # the block moves whole
    assert span(Plan(styled(base, scenes={0: {'text_position': 1}}), 1080, images, logo)) == span(plain)
    assert high.layout(0)['position'] == 0
    high.frame(high.settled(0))


def test_text_position_is_ignored_where_the_look_sizes_the_photo_to_the_text(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    for look in ('politics', 'clean', 'editorial', 'hot', 'pop_art'):
        base = reel.model_copy(update=dict(look=look))
        plain = Plan(base, 360, images, logo)
        moved = Plan(styled(base, scenes={0: {'text_position': 0}}), 360, images, logo)
        assert [e.y for e in moved.scene_elements[0]] == [e.y for e in plain.scene_elements[0]], look
        assert moved.positions[0] is None
    with pytest.raises(ValidationError):
        Scene(text_position=1.5)


def test_api_layout(client, reel):
    response = client.post('/api/reel-creator/layout?scene=1', json=reel.model_dump())
    assert response.status_code == 200
    assert {'boxes', 'position'} == set(response.json())
    assert client.post('/api/reel-creator/layout?scene=5', json=reel.model_dump()).status_code == 400


# --- photo credits and house styles ---------------------------------------

@pytest.mark.parametrize('look', list(LOOKS))
@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
def test_photo_credit_sits_on_the_photo_clear_of_text_and_reels_buttons(look, ratio, creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    value = styled(reel, scenes={0: {'credit': 'Photo: AP / Jane Doe'}}).model_copy(update=dict(look=look, ratio=ratio))
    plan = Plan(value, 1080, images, logo)
    credit = [e for e in plan.scene_elements[0] if e.role == 'credit']
    assert len(credit) == 1
    tile, x, y = credit[0].tile, credit[0].x, credit[0].y
    assert x + tile.width <= plan.W - plan.px(plan.right) + plan.px(10)          # clear of the Reels buttons
    assert plan.px(plan.inset + 110) <= y and y + tile.height <= plan.px(plan.limit)
    rx, ry, rw, rh, _ = plan.rects[0]
    assert rx <= x and x + tile.width <= rx + rw and ry <= y and y + tile.height <= ry + rh   # on the photo
    for element in plan.scene_elements[0]:
        if element.role in ('headline', 'detail', 'lead'):                     # never over the words
            assert y + tile.height <= element.y or element.y + element.tile.height <= y or x >= element.x + element.tile.width, look


def test_photo_credit_needs_a_photo_and_can_be_styled(creator, reel):
    images, logo, _ = creator.validate_assets(reel)
    value = styled(reel, scenes={0: {'credit': 'Photo: AP'}, 1: {'credit': 'Handout'}})   # scene 2 has no photo
    plan = Plan(value, 360, images, logo)
    assert [e.role for e in plan.scene_elements[0]].count('credit') == 1
    assert 'credit' not in {e.role for e in plan.scene_elements[1]}
    assert 'credit' in {box['role'] for box in plan.layout(0)['boxes']}
    hidden = Plan(styled(value, {'credit': {'hidden': True}}), 360, images, logo)
    assert 'credit' not in {e.role for e in hidden.scene_elements[0]}
    with pytest.raises(ValidationError):
        Scene(credit='x' * 61)
    assert Scene(credit='  Photo:   AP ').credit == 'Photo: AP'


def test_house_styles_are_saved_validated_and_deleted(client):
    style = dict(id='c' * 32, name='  Our  breaking ', look='breaking', tag='LIVE NOW', accent='#FF0000', ratio='4:5',
                 motion='none', show_progress=False, music='off', style={'headline': {'color': '#ffee00'}})
    saved = client.put(f'/api/reel-creator/house-styles/{"c" * 32}', json=style)
    assert saved.status_code == 200, saved.text
    assert saved.json() == [dict(style, name='Our breaking', accent='#ff0000')]
    assert client.get('/api/reel-creator/house-styles').json()[0]['style'] == {'headline': {'color': '#ffee00'}}
    clash = client.put(f'/api/reel-creator/house-styles/{"d" * 32}', json=dict(style, id='d' * 32, name='our BREAKING'))
    assert clash.status_code == 400 and 'already called' in clash.json()['detail']
    for bad in (dict(music='custom'), dict(accent='red'), dict(name=''), dict(look='nope'), dict(scenes=[])):
        assert client.put(f'/api/reel-creator/house-styles/{"e" * 32}', json={**style, 'id': 'e' * 32, 'name': 'Other', **bad}).status_code in (400, 422), bad
    assert client.put(f'/api/reel-creator/house-styles/{"f" * 32}', json=dict(style, name='Mismatch')).status_code == 400
    assert client.delete(f'/api/reel-creator/house-styles/{"c" * 32}').json() == []


def test_house_styles_are_limited(creator):
    from backend.services.reel_creator.reel import MAX_HOUSE_STYLES
    for k in range(MAX_HOUSE_STYLES):
        creator.store.save_house_style(dict(id=f'{k:032x}', name=f'Style {k}'), MAX_HOUSE_STYLES)
    with pytest.raises(ValueError, match='up to'):
        creator.store.save_house_style(dict(id='f' * 32, name='One more'), MAX_HOUSE_STYLES)
    creator.store.save_house_style(dict(id=f'{0:032x}', name='Renamed'), MAX_HOUSE_STYLES)      # replacing one is fine


@pytest.mark.parametrize('look', ['politics', 'clean', 'editorial'])
def test_a_scene_without_a_photo_is_the_page_with_its_text_mid_frame(look):
    """No empty photo frame on page looks: the page fills the scene and the text moves toward the middle."""
    with_color = Plan(Reel(look=look, scenes=[Scene(headline='Government approves aid', detail='The money goes to housing.', backdrop='#334455')]), 360, {}, None)
    bare = Plan(Reel(look=look, scenes=[Scene(headline='Government approves aid', detail='The money goes to housing.')]), 360, {}, None)
    assert bare.rects[0][3] == 0 and with_color.rects[0][3] > 0
    assert bare.background(0, 1).getpixel((180, 600)) == bare.page
    top = min(e.y for e in bare.scene_elements[0] if e.role == 'headline')
    assert bare.px(bare.inset + 200) < top < bare.px(bare.limit) * .6


def test_hot_keeps_the_detail_under_its_headline_and_no_source_repeats_the_brand(creator, reel):
    reel.look, reel.source = 'hot', reel.brand
    images, logo, _ = creator.validate_assets(reel)
    plan = Plan(reel, 1080, images, logo)
    elements = plan.scene_elements[0]
    sticker = next(e for e in elements if e.role == 'detail')
    underline = next(e for e in elements if e.role == 'headline' and e.anim == 'wipe')
    assert 0 < sticker.y - underline.y < plan.px(80)                    # right under the headline, not at the bottom
    assert [e.role for e in elements].count('label') == 1               # the tag only: the source is the brand
    reel.source = 'City Wire'
    assert [e.role for e in Plan(reel, 1080, images, logo).scene_elements[0]].count('label') == 2


# --- rewriting one scene ---------------------------------------------------

def rewrite(**extra):
    return request(**{'existing': ['Council backs flood relief plan'], 'position': 2, 'replacing': 'Payments start next week', **extra})


def test_rewriting_one_scene_returns_that_scene_only_in_the_kind_asked_for():
    asked = []
    stat = dict(kind='stat', stat='7-2', headline='Council vote on the plan', source_quote='voted 7 to 2 on Tuesday')
    result = write_reel(rewrite(rewrite='stat'), provider=lambda r: asked.append(r.provider_context()) or copy(scenes=[stat]))
    assert [(s['kind'], s['stat'], s['headline']) for s in result['scenes']] == [('stat', '7-2', 'Council vote on the plan')]
    assert result['hooks'] == [] and result['end'] is None and result['caption'] == ''
    context = asked[0]
    assert context['rewrite_kind'] == 'stat' and context['scene_count'] == 1 and context['rewrite_position'] == 2
    assert context['existing_headlines'] == ['Council backs flood relief plan'] and context['replacing'] == 'Payments start next week'


def test_a_rewrite_never_invents_a_quote_or_figure_the_article_lacks():
    calls = []
    with pytest.raises(ValueError, match='no direct quote'):          # no quotation marks at all: the AI is not asked
        write_reel(rewrite(rewrite='quote'), provider=lambda r: calls.append(1) or copy(scenes=[]))
    assert calls == []
    quoting = WriteRequest(api_key='k', model='gemini-test', text=ARTICLE + ' "We will review every claim quickly," the mayor said.', rewrite='quote', position=2)
    with pytest.raises(ValueError, match='no direct quote'):          # the model says there is none: asked once
        write_reel(quoting, provider=lambda r: calls.append(1) or copy(scenes=[]))
    assert len(calls) == 1
    paraphrase = dict(kind='quote', headline='We must help families now', attribution='Mayor Lina Chhay',
                      source_quote='voted 7 to 2 on Tuesday')
    with pytest.raises(ValueError, match='no direct quote'):          # words not in the article are not a quote
        write_reel(rewrite(rewrite='quote'), provider=lambda r: copy(scenes=[paraphrase]))
    with pytest.raises(ValueError, match='no clear figure'):
        write_reel(rewrite(rewrite='stat'), provider=lambda r: copy(scenes=[dict(kind='stat', stat='many', headline='Many provinces',
                                                                               source_quote='covering three provinces')]))


def test_the_hook_scene_stays_a_story_and_an_end_card_can_be_rewritten():
    story = dict(headline='Flood relief approved', source_quote='voted 7 to 2 on Tuesday')
    assert write_reel(rewrite(rewrite='story', position=1), provider=lambda r: copy(scenes=[story]))['scenes'][0]['kind'] == 'story'
    end = dict(headline='Should relief come first?', detail='', highlight='relief')
    result = write_reel(rewrite(rewrite='end'), provider=lambda r: copy(end_card=end))
    assert result['scenes'] == [] and result['end']['headline'] == 'Should relief come first?'
    with pytest.raises(ValueError):
        write_reel(rewrite(rewrite='end'), provider=lambda r: copy(end_card=None))


def test_reported_speech_never_becomes_a_quote_scene():
    text = ('Mayor Ana Teller said the Route 9 bridge would stay closed until engineers inspect it. '
            '"We will not take any chances with people\'s lives," Teller told reporters on Wednesday evening.')
    reported = dict(kind='quote', headline='the Route 9 bridge would stay closed until engineers inspect it',
                    attribution='Mayor Ana Teller', source_quote='the Route 9 bridge would stay closed')
    quoted = dict(kind='quote', headline="We will not take any chances with people's lives", attribution='Mayor Ana Teller',
                  source_quote='We will not take any chances')
    ask = lambda **extra: WriteRequest(api_key='k', model='gemini-test', text=text, **extra)
    with pytest.raises(ValueError, match='no direct quote'):
        write_reel(ask(rewrite='quote', position=2), provider=lambda r: copy(scenes=[reported]))
    assert write_reel(ask(rewrite='quote', position=2), provider=lambda r: copy(scenes=[quoted]))['scenes'][0]['kind'] == 'quote'
    story = dict(headline='Bridge stays shut for safety checks', source_quote='the Route 9 bridge would stay closed')
    written = write_reel(ask(scenes=2), provider=lambda r: copy(scenes=[story, reported]))
    assert [s['kind'] for s in written['scenes']] == ['story', 'story']     # creating a reel follows the same rule
