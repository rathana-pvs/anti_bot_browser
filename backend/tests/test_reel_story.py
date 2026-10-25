"""Real motion/filter checks in private temporary directories, never publishing."""
import io
import json
from pathlib import Path
import subprocess
import time

from PIL import Image
import psutil
import pytest
from pydantic import ValidationError
from backend.services.reel_creator.models import Composition, Photo, RenderInput
from backend.services.reel_creator.story import timeline, photos_for, save_story, story_command, story_poster, movement_for
from backend.services.reel_creator.service import ReelCreator, tool


def spec_for(tmp_path, count=2, **changes):
    paths=[]
    for i in range(count):
        path=tmp_path/f'input-{i}.png'
        Image.new('RGB',(720,540), ['#aa4433','#3377bb','#44aa77','#7755bb','#bbbb44'][i]).save(path)
        paths.append(path)
    photos=[Photo(image_id=f'{i+1:032x}') for i in range(count)]
    return Composition(version=2,image_id=photos[0].image_id,photos=photos,
        headline='A clear news headline',supporting='First source fact\nSecond source fact',category='NEWS UPDATE',
        duration=20 if count>3 else 10,**changes),paths


def test_timeline_is_frame_exact_and_legacy_stays_legacy(tmp_path):
    for count in range(1,6):
        spec,_=spec_for(tmp_path,count)
        scenes=timeline(spec)
        assert sum(s['hold'] for s in scenes)==spec.duration*30
        assert scenes[-1]['start']+scenes[-1]['frames']==spec.duration*30
        assert all(s['hold']>=90 for s in scenes)
    legacy=Composition(image_id='1'*32)
    assert legacy.version==1 and len(photos_for(legacy))==1
    with pytest.raises(ValidationError): Composition(version=1,image_id='1'*32,photos=[Photo(image_id='1'*32)])
    with pytest.raises(ValidationError): Composition(version=2,image_id='2'*32,photos=[Photo(image_id='1'*32)])
    with pytest.raises(ValidationError): Composition(version=2,image_id='1'*32,photos=[Photo(image_id='1'*32)]*5,duration=10)


def test_independent_text_layers_and_poster_selection(tmp_path):
    spec,paths=spec_for(tmp_path)
    images,text,notes=save_story(spec,paths,None,tmp_path/'layers')
    assert len(images)==2 and [name for name,_ in text]==['panel','category','headline','highlight0','highlight1','brand']
    first,_=story_poster(spec,paths,photo_index=0)
    second,_=story_poster(spec,paths,photo_index=1)
    assert first.size==(540,960) and first.tobytes()!=second.tobytes()
    with pytest.raises(ValueError): story_poster(spec,paths,photo_index=4)
    spec.headline='ព័ត៌មានថ្មីៗ\nNews update'
    assert story_poster(spec,paths)[0]


@pytest.mark.skipif(not tool('ffmpeg'),reason='FFmpeg not installed')
@pytest.mark.parametrize('count,motion,style',[(1,True,'bulletin'),(2,True,'politics'),(5,True,'story'),(3,False,'bulletin')])
def test_actual_story_decodes_duration_and_scene_order(tmp_path,count,motion,style):
    spec,paths=spec_for(tmp_path,count,motion=motion,motion_style=style)
    images,text,_=save_story(spec,paths,None,tmp_path/'layers')
    out=tmp_path/'story.mp4'
    cmd=story_command(tool('ffmpeg'),images,text,out,spec,preview=True)
    process=psutil.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    peak=0;started=time.monotonic()
    while process.poll() is None:
        try:peak=max(peak,process.memory_info().rss)
        except psutil.NoSuchProcess:pass
        if time.monotonic()-started>100:
            process.kill();pytest.fail('Preview exceeded 100 seconds')
        time.sleep(.05)
    error=process.stderr.read().decode()
    assert process.returncode==0,error
    meta=json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(out)]))
    assert (meta['streams'][0]['width'],meta['streams'][0]['height'])==(360,640)
    assert abs(float(meta['format']['duration'])-spec.duration)<.1
    assert peak < 768*1024*1024,peak
    subprocess.run([tool('ffmpeg'),'-v','error','-xerror','-i',str(out),'-f','null','-'],check=True,timeout=30)
    for index in range(count):
        # The center of the full image card is above all text; each scene has its source color.
        seek=(index+.5)*spec.duration/count
        frame=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss',str(seek),'-i',str(out),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        pixel=Image.open(io.BytesIO(frame)).getpixel((180,240))
        expected=Image.open(paths[index]).getpixel((0,0))
        assert max(abs(a-b) for a,b in zip(pixel,expected))<14,(index,pixel,expected)
    if count == 1 and motion:
        whites=[]
        for at in (0.03, 1.0):
            raw=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss',str(at),'-i',str(out),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
            region=Image.open(io.BytesIO(raw)).convert('RGB').crop((20,428,335,550))
            data=region.tobytes()
            whites.append(sum(min(data[i:i+3])>220 for i in range(0,len(data),3)))
        assert whites[1]>whites[0]+20,whites
    print(f'{count} photos: {time.monotonic()-started:.1f}s, peak={peak/1024/1024:.1f}MiB')

@pytest.mark.skipif(not tool('ffmpeg'),reason='FFmpeg not installed')
def test_real_five_photo_export_is_bounded_verified_and_durable(tmp_path):
    import uuid
    from backend.services.reel_creator.audio_presets import preset_path
    original,paths=spec_for(tmp_path,5)
    creator=ReelCreator(tmp_path/'creator',tmp_path/'media',admission=lambda:None)
    photos=[]
    for path in paths:
        asset=creator.import_asset(path.read_bytes(),path.name,'image')
        photos.append(Photo(image_id=asset['id']))
        assert creator.thumbnail_path(asset['id']).is_file()
    spec=original.model_copy(update={'photos':photos,'image_id':photos[0].image_id,'duration':15,'audio_preset':'headlines'})
    creator.start()
    try:
        queued=creator.enqueue(RenderInput(composition=spec,request_id=str(uuid.uuid4()),post_caption='Saved post caption'))
        started=time.monotonic()
        while time.monotonic()-started<160:
            job=creator.store.job(queued['id'])
            if job['state'] not in ('queued','rendering','verifying'):break
            time.sleep(.2)
        assert job['state']=='completed',job
        output=creator.output_path(job)
        meta=json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(output)]))
        assert {s['codec_name'] for s in meta['streams']}=={'h264','aac'}
        assert (meta['streams'][0]['width'],meta['streams'][0]['height'])==(1080,1920)
        assert abs(float(meta['format']['duration'])-15)<.1
        assert job['composition']['photos'][4]['image_id']==photos[4].image_id
        assert job['post_caption']=='Saved post caption'
        assert job['peak_rss_bytes']<768*1024*1024
        print(f'Full five-photo export: {time.monotonic()-started:.1f}s, peak={job["peak_rss_bytes"]/1024/1024:.1f}MiB')
    finally:creator.stop()


def test_wrapped_single_fact_animates_as_one_unit(tmp_path):
    spec,paths=spec_for(tmp_path,1)
    spec.supporting='This single supporting fact wraps onto another line but should animate as one complete thought.'
    _,text,_=save_story(spec,paths,None,tmp_path/'layers')
    assert [name for name,_ in text if name.startswith('highlight')]==['highlight0']


def test_capability_detects_missing_story_filters(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.services.reel_creator import service as module
    monkeypatch.setattr(module,'tool',lambda name:f'/fake/{name}')
    def run(command,**kwargs):
        output='ffmpeg version test' if '-version' in command else ' libx264 aac ' if '-encoders' in command else ' ... overlay V->V'
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(module.subprocess,'run',run)
    caps=ReelCreator(tmp_path/'creator',tmp_path/'media').capabilities()
    assert caps['story_motion'] and caps['max_photos']==5
    assert not caps['ready'] and 'FFmpeg story motion filters' in caps['missing']


def test_motion_presets_respect_manual_overrides_and_static_mode(tmp_path):
    from backend.services.reel_creator.story import text_cue
    spec, _ = spec_for(tmp_path)
    photo = spec.photos[0]
    for style in ('bulletin', 'politics', 'story'):
        spec.motion_style = style
        photo.movement = 'pan_left'
        assert movement_for(spec, photo, 0) == 'pan_left'
        photo.movement = 'auto'
        spec.slideshow_effect = 'still'
        assert movement_for(spec, photo, 0) == 'still'
        spec.slideshow_effect = 'auto'
    spec.motion_style = 'story'
    scenes = timeline(spec)
    assert text_cue(spec, 'highlight1', scenes) == scenes[1]['start'] / spec.fps + .35
    spec.motion = False
    assert movement_for(spec, photo, 0) == 'still'
    assert all(scene['transition'] == 0 for scene in timeline(spec))


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg not installed')
def test_presets_render_visibly_different_photo_motion(tmp_path):
    from PIL import ImageDraw, ImageChops, ImageStat
    from itertools import combinations
    # Textured geometry exposes zoom and pan differences that solid photos hide.
    image = Image.new('RGB', (720, 960), '#1d324a')
    draw = ImageDraw.Draw(image)
    for x in range(0, 720, 60):
        draw.rectangle((x, 0, x + 24, 960), fill=(80 + x // 6, 150, 210))
    for y in range(40, 960, 100):
        draw.ellipse((200, y, 420, y + 65), fill='#eeaa44')
    path = tmp_path / 'texture.png'
    image.save(path)
    base = Composition(version=2, image_id='1'*32, photos=[Photo(image_id='1'*32)],
                       duration=5, fps=24, treatment='fill', headline='Preset motion')
    images, text, _ = save_story(base, [path], None, tmp_path / 'layers')
    frames = {}
    for style in ('bulletin', 'politics', 'story'):
        spec = base.model_copy(update={'motion_style': style})
        output = tmp_path / f'{style}.mp4'
        result = subprocess.run(story_command(tool('ffmpeg'), images, text, output, spec, preview=True),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=40)
        assert result.returncode == 0, result.stderr.decode()
        samples = []
        for at in (1, 4):
            raw = subprocess.check_output([tool('ffmpeg'), '-v', 'error', '-ss', str(at), '-i', str(output),
                                           '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], timeout=15)
            samples.append(Image.open(io.BytesIO(raw)).convert('RGB').crop((20, 80, 340, 380)))
        assert sum(ImageStat.Stat(ImageChops.difference(*samples)).mean) / 3 > .5
        frames[style] = samples[-1]
    for first, second in combinations(frames, 2):
        difference = sum(ImageStat.Stat(ImageChops.difference(frames[first], frames[second])).mean) / 3
        assert difference > 2, (first, second, difference)


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg not installed')
@pytest.mark.parametrize('animation,axis,sign', [
    ('slide', 1, 1), ('slide_down', 1, -1),
    ('slide_left', 0, -1), ('slide_right', 0, 1),
])
def test_text_entrances_move_in_selected_direction_and_settle(tmp_path, animation, axis, sign):
    import numpy as np
    photo = tmp_path / 'black.png'
    Image.new('RGB', (720, 1280), 'black').save(photo)
    spec = Composition(version=2, image_id='1'*32, photos=[Photo(image_id='1'*32, movement='still')],
                       headline='NEWS', duration=5, fps=24, treatment='fill', text_animation=animation)
    images, text, _ = save_story(spec, [photo], None, tmp_path / 'layers')
    output = tmp_path / 'entrance.mp4'
    result = subprocess.run(story_command(tool('ffmpeg'), images, text, output, spec, preview=True),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=40)
    assert result.returncode == 0, result.stderr.decode()
    centers = []
    for at in (4 / 24, 1, 4):
        raw = subprocess.check_output([tool('ffmpeg'), '-v', 'error', '-ss', str(at), '-i', str(output),
                                       '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], timeout=15)
        pixels = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'))[400:580, 10:350]
        # Only neutral headline pixels; exclude the red accent and progress strip.
        brightness = pixels.min(axis=2)
        ys, xs = np.where(brightness > max(12, brightness.max() * .5))
        assert len(xs) > 30
        centers.append((xs.mean(), ys.mean()))
    assert (centers[0][axis] - centers[1][axis]) * sign > 1.5, centers
    assert max(abs(a - b) for a, b in zip(centers[1], centers[2])) < 1, centers
