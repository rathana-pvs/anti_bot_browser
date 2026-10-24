"""Slideshow cycles, subtitle layout and actual export settings; no publishing."""
import io
import json
import subprocess
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageChops
import pytest
from pydantic import ValidationError

from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.story import timeline, overlays, save_story, story_command
from backend.services.reel_creator.composition import fit_text, make_layers, save_layers, encode_command
from backend.services.reel_creator.service import tool


def spec(**changes):
    return Composition(version=2, image_id='1'*32, photos=[Photo(image_id='1'*32)],
                       slide_seconds=5, **changes)


@pytest.mark.parametrize('fps', [24,30,60])
def test_cycles_repeat_each_photo_at_five_seconds_and_clip_final_scene(fps):
    value=spec(fps=fps,duration=15)
    value.photos.append(Photo(image_id='2'*32))
    scenes=timeline(value)
    assert [s['photo_index'] for s in scenes]==[0,1,0]
    assert [s['start'] for s in scenes]==[0,5*fps,10*fps]
    assert scenes[-1]['start']+scenes[-1]['frames']==15*fps
    value.slide_seconds=7
    assert [s['hold']/fps for s in timeline(value)]==[7,7,1]


def test_old_drafts_retain_even_timing_and_invalid_settings_are_rejected():
    old=Composition(version=2,image_id='1'*32,photos=[Photo(image_id='1'*32)]*2,duration=15)
    assert old.slide_seconds is None
    assert [s['hold'] for s in timeline(old)]==[225,225]
    for changes in [dict(fps=25),dict(resolution=2160),dict(slide_seconds=2),dict(duration=5,photos=[Photo(image_id='1'*32)]*2)]:
        with pytest.raises(ValidationError):
            Composition(version=2,image_id='1'*32,slide_seconds=5,**changes) if 'slide_seconds' not in changes else Composition(**changes)


def test_subtitle_toggle_preserves_copy_and_enlarges_headline_in_both_layouts(tmp_path):
    image=tmp_path/'photo.png';Image.new('RGB',(600,800),'blue').save(image)
    value=spec(headline='News update',supporting='Keep this subtitle')
    with patch('backend.services.reel_creator.composition.fit_text',wraps=fit_text) as fit:
        visible=overlays(value)
        assert any(name.startswith('highlight') for name,_ in visible)
        assert next(c.args[3] for c in fit.call_args_list if c.args[0]==value.headline)==66
    value.show_subtitle=False
    with patch('backend.services.reel_creator.composition.fit_text',wraps=fit_text) as fit:
        hidden=overlays(value)
        assert not any(name.startswith('highlight') for name,_ in hidden)
        assert next(c.args[3] for c in fit.call_args_list if c.args[0]==value.headline)==76
    with patch('backend.services.reel_creator.composition.fit_text',wraps=fit_text) as fit:
        make_layers(value,image)
        assert not any(c.args[0]==value.supporting for c in fit.call_args_list)
        assert next(c.args[3] for c in fit.call_args_list if c.args[0]==value.headline)==76
    assert value.supporting=='Keep this subtitle'


@pytest.mark.skipif(not tool('ffmpeg'),reason='FFmpeg not installed')
@pytest.mark.parametrize('fps,resolution,effect,version',[(24,720,'zoom_in',2),(60,1080,'fade',2),(30,720,'still',1)])
def test_real_exports_use_requested_dimensions_fps_and_repeat_single_photo(tmp_path,fps,resolution,effect,version):
    image=tmp_path/'photo.png'
    canvas=Image.new('RGB',(540,960),'#245577')
    draw=ImageDraw.Draw(canvas)
    for x in range(0,540,45):draw.rectangle((x,0,x+20,960),fill='#aabbcc')
    canvas.save(image)
    value=spec(fps=fps,resolution=resolution,slideshow_effect=effect,show_subtitle=False)
    if version==1:value=value.model_copy(update={'version':1,'photos':[],'slide_seconds':None})
    out=tmp_path/'result.mp4'
    if version==2:
        images,text,_=save_story(value,[image],None,tmp_path/'layers')
        cmd=story_command(tool('ffmpeg'),images,text,out,value)
    else:
        cmd=encode_command(tool('ffmpeg'),save_layers(make_layers(value,image),tmp_path/'layers'),out,value)
    result=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=150)
    assert result.returncode==0,result.stderr.decode()
    meta=json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(out)]))
    video=meta['streams'][0]
    assert (video['width'],video['height'])==(resolution,1280 if resolution==720 else 1920)
    assert video['avg_frame_rate']==f'{fps}/1'
    assert abs(float(meta['format']['duration'])-10)<.05
    subprocess.run([tool('ffmpeg'),'-v','error','-xerror','-i',str(out),'-f','null','-'],check=True,timeout=60)
    if version==2:
        def frame(at):
            raw=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss',str(at),'-i',str(out),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
            return Image.open(io.BytesIO(raw)).convert('RGB').crop((0,100,resolution,900))
        first,repeat=frame(2),frame(7)
        import numpy as np
        assert np.abs(np.asarray(first,dtype=float)-np.asarray(repeat,dtype=float)).mean()<2
        if effect=='zoom_in':assert ImageChops.difference(first,frame(4)).getbbox()
        if effect=='fade':assert np.asarray(frame(.05)).mean()<np.asarray(first).mean()*.4
