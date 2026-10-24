import hashlib
import io
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from backend.routers import reel_creator as router
from backend.services.reel_creator.models import Composition, CropRect, Photo
from backend.services.reel_creator.framing import crop_image
from backend.services.reel_creator.service import ReelCreator, tool
from backend.services.reel_creator.story import photo_frame, save_story, story_command


def fixture_image():
    image = Image.new('RGB', (1000, 800), '#00c800')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 999, 99), fill='red')
    draw.rectangle((0, 600, 999, 799), fill='blue')
    return image


def test_crop_validation_and_saved_draft_roundtrip():
    for value in [dict(left=-.1),dict(right=1.1),dict(top=.9,bottom=.8),dict(left=.49,right=.5),dict(bottom=float('nan'))]:
        with pytest.raises(ValueError):CropRect(**value)
    crop = CropRect(top=.125,bottom=.75)
    spec = Composition(version=2,image_id='1'*32,photos=[Photo(image_id='1'*32,crop=crop),Photo(image_id='2'*32)])
    restored=Composition.model_validate_json(spec.model_dump_json())
    assert restored.photos[0].crop==crop
    assert restored.photos[1].crop is None
    assert Photo(image_id='1'*32).crop is None


def test_crop_removes_banners_without_changing_original():
    image=fixture_image(); before=image.tobytes()
    cropped=crop_image(image,CropRect(top=.125,bottom=.75))
    assert cropped.size==(1000,500)
    assert cropped.getextrema()==((0,0),(200,200),(0,0))
    assert image.tobytes()==before
    assert crop_image(image,None) is image


def test_crop_preview_and_face_analysis_are_validated_and_non_destructive(tmp_path,monkeypatch):
    creator=ReelCreator(tmp_path/'creator',tmp_path/'media',admission=lambda:None)
    try:
        data=io.BytesIO();fixture_image().save(data,'PNG')
        asset=creator.import_asset(data.getvalue(),'source.png','image')
        path=creator.asset_path(asset['id'],'image');before=hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(router,'get_service',lambda:creator)
        from backend.services.reel_creator import auto_framing
        seen=[]
        monkeypatch.setattr(auto_framing,'suggest_framing',lambda image, aspect_ratio='9:16': seen.append(image.size) or {'settings':{'framing':'fit'},'status':'none','faces':0})
        app=FastAPI();app.include_router(router.router)
        with TestClient(app) as client:
            preview=client.get(f'/api/reel-creator/assets/{asset["id"]}/crop-preview')
            assert preview.status_code==200 and Image.open(io.BytesIO(preview.content)).size==(1000,800)
            result=client.post(f'/api/reel-creator/assets/{asset["id"]}/framing',json={'top':.125,'bottom':.75})
            assert result.status_code==200 and seen==[(1000,500)]
            assert client.post(f'/api/reel-creator/assets/{asset["id"]}/framing',json={'top':.95,'bottom':.96}).status_code==422
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    finally:creator.stop()


def test_crop_is_used_in_fit_fill_posters_and_encoded_slideshow(tmp_path):
    path=tmp_path/'source.png';fixture_image().save(path)
    photo=Photo(image_id='1'*32,crop=CropRect(top=.125,bottom=.75),movement='still')
    spec=Composition(version=2,image_id=photo.image_id,photos=[photo],duration=5,slide_seconds=3)
    for mode in ('fit','fill'):
        photo.framing=mode
        frame,_=photo_frame(spec,photo,path)
        assert frame.getpixel((540,980))==(0,200,0)
        # Neither the photo nor its blurred background may use excluded banners.
        assert frame.getpixel((500,1900))[2]<20
        assert frame.getpixel((500,10))[0]<10
    if not tool('ffmpeg'):pytest.skip('FFmpeg unavailable')
    images,text,_=save_story(spec,[path],None,tmp_path/'layers')
    output=tmp_path/'cropped.mp4'
    subprocess.run(story_command(tool('ffmpeg'),images,text,output,spec,preview=True),check=True,capture_output=True,timeout=30)
    for seconds in (.5,3.5):
        raw=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss',str(seconds),'-i',str(output),'-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'])
        frame=Image.open(io.BytesIO(raw))
        red,green,blue=frame.getpixel((180,327))
        assert green>180 and red<10 and blue<10
