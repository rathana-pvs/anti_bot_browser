"""Ratio persistence, native layout and actual encoded dimensions."""
import json
import subprocess

from PIL import Image, ImageDraw
import pytest
from pydantic import ValidationError

from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.layout import canvas_size, output_size, layout_y
from backend.services.reel_creator.composition import make_layers, poster, save_layers, encode_command, fit_news_text
from backend.services.reel_creator.story import save_story, story_command, story_poster
from backend.services.reel_creator.framing import photo_bounds
from backend.services.reel_creator.auto_framing import framing_for_faces
from backend.services.reel_creator.service import tool


def test_legacy_default_and_reject_unsupported_ratio():
    assert canvas_size(Composition()) == (1080, 1920)
    with pytest.raises(ValidationError):
        Composition(aspect_ratio='16:9')
    value = Composition(aspect_ratio='4:5', photos=[Photo(image_id='1'*32,
        framing_by_ratio={'9:16': {'zoom':1.5, 'offset_x':.2}})], version=2, image_id='1'*32)
    restored = Composition.model_validate_json(value.model_dump_json())
    assert restored.aspect_ratio == '4:5'
    assert restored.photos[0].framing_by_ratio['9:16'].zoom == 1.5


@pytest.mark.parametrize('ratio,height', [('9:16',1920), ('4:5',1350), ('1:1',1080)])
def test_dimensions_text_and_photo_geometry(ratio, height):
    spec = Composition(aspect_ratio=ratio, headline='A clear headline for the daily news',
                       supporting='Two facts to explain the story', category='NEWS')
    assert output_size(spec) == (1080,height)
    spec.resolution = 720
    assert output_size(spec) == (720, height*2//3)
    assert output_size(spec, True) == (360, height//3)
    photo = Photo(image_id='1'*32, framing='fit')
    _, (x,y,w,h) = photo_bounds(spec,photo,(600,800))
    assert abs(w/h - 600/800) < 1e-9  # no stretching
    assert x >= 0 and y >= 0 and y+h <= layout_y(spec,1210)+1
    lines,size,sub,subsize = fit_news_text(spec)
    end = layout_y(spec,1430) + len(lines)*round(size*1.55) + 10 + len(sub)*round(subsize*1.55)
    assert end <= layout_y(spec,1800)
    # Suggestions keep faces in the selected canvas's safe photo area.
    settings = framing_for_faces((1200,1600),[(500,300,150,180)],ratio)
    if settings['framing'] == 'fill':
        _, (_,fy,_,fh) = photo_bounds(spec,Photo(image_id='1'*32,**settings),(1200,1600))
        assert fy+300*fh/1600 >= layout_y(spec,230)
        assert fy+480*fh/1600 <= layout_y(spec,1100)


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg unavailable')
@pytest.mark.parametrize('version', [1,2])
@pytest.mark.parametrize('ratio,height,preview', [('9:16',1920,True), ('4:5',1350,False), ('1:1',1080,True)])
def test_real_encode_and_poster_match_ratio(tmp_path,version,ratio,height,preview):
    source = tmp_path/'photo.png'
    image = Image.new('RGB',(600,800),'#234567')
    ImageDraw.Draw(image).ellipse((100,100,500,500),fill='#ffaa44')
    image.save(source)
    spec = Composition(version=version, aspect_ratio=ratio, resolution=720, duration=5, fps=24,
        image_id='1'*32, photos=[Photo(image_id='1'*32)] if version==2 else [],
        headline='A clear headline', supporting='A short supporting line', category='NEWS',
        brand='Daily news', source='Design test', news_design='clean', template='editorial',
        motion=True)
    output = tmp_path/'result.mp4'
    if version==2:
        still,_ = story_poster(spec,[source])
        images,text,_ = save_story(spec,[source],None,tmp_path/'layers')
        command = story_command(tool('ffmpeg'),images,text,output,spec,preview=preview)
    else:
        layers = make_layers(spec,source)
        still = poster(layers)
        command = encode_command(tool('ffmpeg'),save_layers(layers,tmp_path/'layers'),output,spec,preview=preview)
    assert still.size == (540,height//2)
    process = subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
    assert process.returncode==0, process.stderr.decode()
    meta = json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_streams','-of','json',str(output)]))
    video = meta['streams'][0]
    assert (video['width'],video['height']) == output_size(spec,preview)
    assert video['avg_frame_rate']=='24/1'
    assert video['sample_aspect_ratio']=='1:1'
