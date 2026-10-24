"""Lens Report's two layouts, source/caption durability and actual bounded exports."""
import io
import json
import shutil
import subprocess
import uuid
from unittest.mock import patch

import pytest
from PIL import Image
from pydantic import ValidationError

from backend.services.reel_creator.models import Composition, Photo, RenderInput
from backend.services.reel_creator.service import ReelCreator
from backend.services.reel_creator.lens_report import layers, poster, prepare, runtime_status


def story(tmp_path, ratio='9:16', count=2):
    creator=ReelCreator(tmp_path/'creator',tmp_path/'media',admission=lambda:None)
    photos=[]
    for index in range(count):
        image=Image.new('RGB',(600,800),['#9e6648','#477b71','#496f94','#778257','#81708d'][index])
        image.paste('#e6d7b1',(200,100,400,650))
        data=io.BytesIO();image.save(data,'PNG')
        asset=creator.import_asset(data.getvalue(),f'photo-{index}.png','image')
        photos.append(Photo(image_id=asset['id'],headline=f'A quieter place {index+1}',caption=f'Community spaces welcome visitors. Scene {index+1}.',framing='fill'))
    spec=Composition(version=3,template='lens_report',aspect_ratio=ratio,photos=photos,image_id=photos[0].image_id,
                     duration=15 if count>2 else 10,fps=30,resolution=1080,source='Fictional demonstration | 23 October 2026',brand='LENS REPORT',category='COMMUNITY')
    return creator,spec


def test_versioned_layout_and_ratio_validation(tmp_path):
    creator,spec=story(tmp_path)
    with pytest.raises(ValidationError):Composition(**{**spec.model_dump(),'version':2})
    with pytest.raises(ValidationError):Composition(**{**spec.model_dump(),'aspect_ratio':'1:1'})
    with pytest.raises(ValidationError):Composition(**{**spec.model_dump(),'layout_revision':99})
    for ratio,expected in [('9:16',(540,960)),('4:5',(540,675))]:
        current=spec.model_copy(update={'aspect_ratio':ratio})
        paths=[creator.asset_path(p.image_id,'image') for p in current.photos]
        picture,_=poster(current,paths)
        assert picture.size==expected
        next_picture,_=poster(current,paths,photo_index=1)
        assert picture.tobytes()!=next_picture.tobytes()
        with pytest.raises(ValueError,match='source attribution'):poster(current.model_copy(update={'source':''}),paths)
        too_long=spec.photos[0].model_copy(update={'headline':'unbreakable'*24})
        with pytest.raises(ValueError):layers(current,too_long,paths[0])


def test_caps_require_prepared_lens_revision(tmp_path):
    creator,spec=story(tmp_path)
    with patch('backend.services.reel_creator.lens_report.runtime_status',return_value=(False,'Prepare Lens Report runtime')):
        caps=creator.capabilities(True)
        assert not caps['lens_report']
        with pytest.raises(ValueError,match='Prepare Lens Report'):creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec))


def test_caption_snapshot_framing_and_reading_time(tmp_path):
    from backend.services.reel_creator.photo_timing import photo_seconds
    from backend.services.reel_creator.lens_report import motion_for
    creator,spec=story(tmp_path)
    assert photo_seconds(spec)==[4,4]
    request=RenderInput(request_id=uuid.uuid4().hex,composition=spec,post_caption='Reviewed post caption')
    job=creator.enqueue(request)
    assert creator.enqueue(request)['id']==job['id']
    spec.photos[0].caption='Edited after enqueue'
    assert job['composition']['photos'][0]['caption']!='Edited after enqueue'
    assert job['post_caption']==request.post_caption
    assert motion_for(spec,spec.photos[0].model_copy(update={'framing':'fit'}))=='still'
    assert motion_for(spec,spec.photos[0].model_copy(update={'movement':'still'}))=='still'
    assert motion_for(spec,spec.photos[0])=='zoom_in'
    crowded=spec.photos[0].model_copy(update={'caption':' '.join(['word']*35)})
    with pytest.raises(ValueError,match='reading time'):
        poster(spec.model_copy(update={'photos':[crowded],'duration':5}),[creator.asset_path(crowded.image_id,'image')])


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'),reason='Prepared Remotion runtime required')
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_real_preview_ratio_dissolve_and_decode(tmp_path,ratio):
    creator,spec=story(tmp_path,ratio)
    spec=spec.model_copy(update={'fps':24})
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,kind='preview'))
    creator._render(job)
    result=creator.store.job(job['id'])
    assert result['state']=='completed',result.get('error')
    path=creator.output_path(result)
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
    video=meta['streams'][0]
    assert (video['width'],video['height'])==(360,640 if ratio=='9:16' else 450)
    assert int(video['nb_frames'])==spec.duration*spec.fps
    # The midpoint of the dissolve contains both source colors instead of a hard cut.
    values=[]
    for at in [4.5,5.2,6]:
        frame=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        values.append(Image.open(io.BytesIO(frame)).convert('RGB').getpixel((35,100)))
    assert values[0]!=values[1] and values[1]!=values[2]
    assert result.get('peak_rss_bytes',0)<=768*1024*1024
    cancelled=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,kind='preview'))
    creator.store.update(cancelled['id'],cancel_requested=True)
    creator._render(cancelled)
    assert creator.store.job(cancelled['id'])['state']=='cancelled'


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'),reason='Prepared Remotion runtime required')
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_five_photo_1080_export_with_audio(tmp_path,ratio):
    creator,spec=story(tmp_path,ratio,5)
    spec=spec.model_copy(update={'audio_preset':'headlines','layout_revision':2,'accent':'#e02f2f'})
    # Keep each three-second scene short enough to read.
    spec.photos=[p.model_copy(update={'headline':f'Community scene {i+1}','caption':'A fictional photo example.'}) for i,p in enumerate(spec.photos)]
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,post_caption='Saved caption'))
    creator._render(job)
    result=creator.store.job(job['id'])
    assert result['state']=='completed',result.get('error')
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(creator.output_path(result))]))
    assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in meta['streams'])
    assert result['post_caption']=='Saved caption'
    assert result.get('peak_rss_bytes',0)<=768*1024*1024
    assert not list((creator.root/'staging').iterdir())
    print(f"Lens Report 1080 {ratio} peak: {result.get('peak_rss_bytes',0)/1024/1024:.1f} MiB")


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_editorial_palette_and_complete_text_sprites(tmp_path,ratio,theme):
    creator,spec=story(tmp_path,ratio)
    spec=spec.model_copy(update={'layout_revision':2,'accent':'#e02f2f','lens_theme':theme})
    sprites=[]
    prepared=layers(spec,spec.photos[0],creator.asset_path(spec.image_id,'image'),text_sprites=sprites)
    expected=(0,0,0,255) if theme=='dark' else (247,245,239,255)
    assert prepared[1].getpixel((20,20)) == expected
    assert prepared[1].getpixel((20,1300)) == expected
    photo_top=128 if ratio=='4:5' else 210
    assert prepared[1].getpixel((20,photo_top-1)) == expected
    assert prepared[1].getpixel((20,photo_top+1))[3] < 255
    rebuilt=Image.new('RGBA',prepared[2].size)
    for sprite in sprites:
        rebuilt.alpha_composite(sprite['image'],(sprite['x'],sprite['y']))
        sprite['image'].close()
    assert rebuilt.tobytes()==prepared[2].tobytes()
    assert {sprite['kind'] for sprite in sprites}=={'title','body'}
    for layer in prepared:layer.close()


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'),reason='Prepared Remotion runtime required')
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_real_editorial_text_entrance(tmp_path,ratio):
    creator,spec=story(tmp_path,ratio)
    spec=spec.model_copy(update={'layout_revision':2,'motion':True,'text_animation':'auto','accent':'#e02f2f','lens_theme':'dark','show_progress':True})
    spec.photos=[photo.model_copy(update={'movement':'still'}) for photo in spec.photos]
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,kind='preview'))
    creator._render(job)
    result=creator.store.job(job['id'])
    assert result['state']=='completed',result.get('error')
    path=creator.output_path(result)
    frames=[]
    for at in [0,.35,1.8]:
        data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        frames.append(Image.open(io.BytesIO(data)).convert('RGB'))
    assert max(frames[2].getpixel((5,5)))<=3
    top=1180 if ratio=='9:16' else 740
    crop=(24,round((top+40)/3),336,round((top+300)/3))
    from PIL import ImageChops
    assert ImageChops.difference(frames[0].crop(crop),frames[1].crop(crop)).getbbox()
    assert ImageChops.difference(frames[1].crop(crop),frames[2].crop(crop)).getbbox()
    for at in [0,2.5,5.2,7.5,9.9]:
        data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        frame=Image.open(io.BytesIO(data)).convert('RGB')
        red=sum(r>g+40 and r>b+40 for r,g,b in [frame.getpixel((x,frame.height-2)) for x in range(frame.width)])
        assert abs(red-frame.width*at/spec.duration)<5,(at,red)
    assert result.get('peak_rss_bytes',0)<=768*1024*1024
    output=__import__('pathlib').Path('tools/news-reel/output/lens-review')
    output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(path,output/f"lens-animated-{ratio.replace(':','x')}.mp4")


def test_lens_theme_and_progress_saved_in_manifest(tmp_path):
    creator,spec=story(tmp_path)
    spec=spec.model_copy(update={'layout_revision':2,'lens_theme':'dark','show_progress':False})
    directory=tmp_path/'prepared';directory.mkdir()
    prepare(spec,[creator.asset_path(p.image_id,'image') for p in spec.photos],None,directory,preview=True)
    data=json.loads((directory/'lens-report.json').read_text())
    assert data['showProgress'] is False
    assert data['progressTrack']=='#34383f'
    restored=Composition.model_validate_json(spec.model_dump_json())
    assert restored.lens_theme=='dark' and restored.show_progress is False
    with pytest.raises(ValidationError):Composition(**{**spec.model_dump(),'lens_theme':'invalid'})


def test_compact_caption_panel_fits_headline_and_caption_together(tmp_path):
    creator,spec=story(tmp_path,'4:5')
    spec=spec.model_copy(update={'layout_revision':2,'duration':20,'lens_theme':'dark'})
    photo=spec.photos[0].model_copy(update={'headline':'International communities strengthen connections through collaborative education and development',
      'caption':'Local families discover new opportunities through shared community spaces.'})
    prepared=layers(spec,photo,creator.asset_path(photo.image_id,'image'))
    for layer in prepared:layer.close()
