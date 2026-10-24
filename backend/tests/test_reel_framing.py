import io
import subprocess

from PIL import Image
from pydantic import ValidationError
import pytest

from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.framing import photo_bounds, transformed_photo
from backend.services.reel_creator.story import photo_frame, save_story, story_command, story_poster
from backend.services.reel_creator.service import tool


def composition(**changes):
    return Composition(version=2,image_id='1'*32,photos=[Photo(image_id='1'*32)],
                       headline='Framing test',duration=5,**changes)


def test_default_framing_preserves_saved_fit_and_fill():
    spec=composition()
    treatment,bounds=photo_bounds(spec,spec.photos[0],(600,800))
    assert treatment=='fit' and bounds==pytest.approx((172.5,230,735,980))
    spec.treatment='fill'
    assert photo_bounds(spec,spec.photos[0],(600,800))[1]==pytest.approx((-180,0,1440,1920))
    spec.photos[0].framing='fit'
    assert photo_bounds(spec,spec.photos[0],(600,800))[0]=='fit'


def test_per_photo_zoom_and_drag_roundtrip_without_changing_other_photos():
    spec=composition()
    spec.photos.append(Photo(image_id='2'*32))
    spec.duration=10
    spec.photos[0]=Photo(image_id='1'*32,framing='fill',zoom=1.5,offset_x=.1,offset_y=-.1)
    restored=Composition.model_validate_json(spec.model_dump_json())
    assert photo_bounds(restored,restored.photos[0],(600,800))[1]==pytest.approx((-432,-672,2160,2880))
    assert restored.photos[1].zoom==1 and restored.photos[1].offset_x==0
    for changes in [dict(zoom=0),dict(zoom=4),dict(offset_x=2),dict(offset_y=float('nan'))]:
        with pytest.raises(ValidationError):Photo(image_id='1'*32,**changes)


def test_transform_is_bounded_even_for_extreme_aspect_and_zoom():
    spec=composition()
    photo=Photo(image_id='1'*32,framing='fill',zoom=3)
    image=Image.new('RGB',(20000,10),'red')
    bounds=photo_bounds(spec,photo,image.size)[1]
    assert bounds[2]>1_000_000
    result=transformed_photo(image,bounds)
    assert result.size==(1080,1920) and result.getpixel((540,960))==(255,0,0,255)


def test_position_and_size_match_the_layout_poster(tmp_path):
    path=tmp_path/'photo.png';Image.new('RGB',(600,800),'#e04020').save(path)
    spec=composition(motion=False)
    spec.photos[0]=Photo(image_id='1'*32,framing='fit',zoom=.5,offset_x=.2,offset_y=-.1)
    bounds=photo_bounds(spec,spec.photos[0],(600,800))[1]
    foreground=transformed_photo(Image.open(path),bounds)
    x,y,w,h=bounds
    assert foreground.getpixel((int(x+10),int(y+10)))[3]==255
    assert foreground.getpixel((int(x-10),int(y+10)))[3]==0
    poster,_=story_poster(spec,[path])
    assert poster.getpixel((int((x+w/2)/2),int((y+h/2)/2)))==(224,64,32)


@pytest.mark.skipif(not tool('ffmpeg'),reason='FFmpeg not installed')
def test_actual_video_uses_saved_photo_transform(tmp_path):
    path=tmp_path/'photo.png';Image.new('RGB',(600,800),'#e04020').save(path)
    spec=composition(motion=False)
    spec.photos[0]=Photo(image_id='1'*32,framing='fit',zoom=.5,offset_x=.2,offset_y=-.1)
    images,text,_=save_story(spec,[path],None,tmp_path/'layers')
    out=tmp_path/'framing.mp4'
    subprocess.run(story_command(tool('ffmpeg'),images,text,out,spec,preview=True),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=60)
    raw=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss','1','-i',str(out),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
    frame=Image.open(io.BytesIO(raw)).convert('RGB')
    x,y,w,h=photo_bounds(spec,spec.photos[0],(600,800))[1]
    actual=frame.getpixel((int((x+w/2)/3),int((y+h/2)/3)))
    assert max(abs(a-b) for a,b in zip(actual,(224,64,32)))<10
    # This location is in the old centered image but outside the new smaller image.
    assert frame.getpixel((100,160))[0]<180
