"""Per-photo copy and reading-time schedules, including real encoded boundaries."""
import io
import json
import subprocess

import pytest
from PIL import Image, ImageChops, ImageStat
from pydantic import ValidationError

from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.photo_timing import photo_seconds, reading_seconds
from backend.services.reel_creator.story import timeline, save_story, story_command, story_poster
from backend.services.reel_creator.service import tool


def composition(**changes):
    values = dict(version=2, image_id='1'*32, auto_timing=True, headline='Main headline',
                  photos=[Photo(image_id='1'*32), Photo(image_id='2'*32)])
    return Composition(**(values | changes))


def test_reading_duration_custom_copy_fallback_and_manual_overrides():
    assert [reading_seconds('word ' * count) for count in (1, 6, 7, 12, 13, 20, 21)] == [3,3,5,5,7,7,10]
    assert reading_seconds('ក' * 70) == 7  # No whitespace in a Khmer headline.
    value = composition(photos=[Photo(image_id='1'*32), Photo(image_id='2'*32, headline='word '*13)])
    assert photo_seconds(value) == [3,7] and value.duration == 10
    value = Composition(**(value.model_dump() | {'photos': [
        dict(image_id='1'*32, duration_seconds=4), dict(image_id='2'*32, headline='word '*7)]}))
    assert photo_seconds(value) == [4,5] and value.duration == 9
    assert sum(scene['hold'] for scene in timeline(value)) == 9 * value.fps
    saved = Composition.model_validate_json(value.model_dump_json())
    assert saved.photos[1].headline == value.photos[1].headline
    assert saved.photos[0].duration_seconds == 4
    assert saved.duration == 9


def test_narration_uses_measured_length_and_keeps_manual_photos():
    value = composition(narration_enabled=True, narration_id='3'*32, narration_duration=12.2)
    assert value.duration == 13 and photo_seconds(value) == [7,6]
    value = composition(narration_enabled=True, narration_id='3'*32, narration_duration=12.2,
                        photos=[Photo(image_id='1'*32, duration_seconds=4), Photo(image_id='2'*32)])
    assert photo_seconds(value) == [4,9]
    with pytest.raises(ValidationError, match='shorter than narration'):
        composition(narration_enabled=True, narration_id='3'*32, narration_duration=12.2,
                    photos=[Photo(image_id='1'*32, duration_seconds=4), Photo(image_id='2'*32, duration_seconds=4)])
    with pytest.raises(ValidationError, match='too long'):
        composition(narration_enabled=True, narration_id='3'*32, narration_duration=60)


def test_bad_photo_values_and_legacy_timing_are_preserved():
    with pytest.raises(ValidationError): Photo(image_id='1'*32, duration_seconds=2)
    with pytest.raises(ValidationError): Photo(image_id='1'*32, duration_seconds=4.5)
    with pytest.raises(ValidationError): Photo(image_id='1'*32, headline='bad\x00text')
    with pytest.raises(ValidationError): Photo(image_id='1'*32, headline='x'*241)
    value = composition(auto_timing=False, slide_seconds=5, duration=15)
    assert [s['photo_index'] for s in timeline(value)] == [0,1,0]
    with pytest.raises(ValidationError): composition(auto_timing=False, duration=9)


@pytest.mark.skipif(not tool('ffmpeg'), reason='FFmpeg not installed')
@pytest.mark.parametrize('auto,motion,preview', [(True,False,True), (True,True,False), (False,False,True)])
def test_encoded_headlines_follow_photo_boundaries_and_repeat(tmp_path, auto, motion, preview):
    paths=[]
    for i in range(2):
        path=tmp_path/f'photo{i}.png'
        Image.new('RGB',(720,1280),('#18354a','#382434')[i]).save(path)
        paths.append(path)
    value = composition(auto_timing=auto, motion=motion, slide_seconds=None if auto else 3,
                        duration=10, fps=24, resolution=720, text_animation='slide_left' if motion else 'none',
                        photos=[Photo(image_id='1'*32, headline='FIRST', movement='still'),
                                Photo(image_id='2'*32, headline='Second photo has a longer headline here', movement='still')])
    assert value.duration == (8 if auto else 10)
    images,text,_=save_story(value,paths,None,tmp_path/'layers')
    output=tmp_path/'photo-headlines.mp4'
    result=subprocess.run(story_command(tool('ffmpeg'),images,text,output,value,preview=preview),
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=45)
    assert result.returncode==0,result.stderr.decode()
    meta=json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-show_format','-of','json',str(output)]))
    assert abs(float(meta['format']['duration'])-value.duration)<.1
    samples=[(1.5,0),(4.5,1)] + ([] if auto else [(7.5,0),(9.5,1)])
    for at,index in samples:
        raw=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss',str(at),'-i',str(output),'-frames:v','1','-f','image2pipe','-vcodec','png','-'],timeout=15)
        actual=Image.open(io.BytesIO(raw)).convert('RGB')
        poster,_=story_poster(value,paths,photo_index=index)
        expected=poster.resize(actual.size,Image.Resampling.LANCZOS)
        w,h=actual.size
        region=(round(w*.04),round(h*.625),round(w*.96),round(h*.922))
        error=sum(ImageStat.Stat(ImageChops.difference(actual.crop(region),expected.crop(region))).mean)/3
        assert error<5,(at,index,error)
