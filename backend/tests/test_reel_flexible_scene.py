"""Flexible layouts exercise real photo placement, motion, and transitions."""
import io,json,shutil,subprocess,uuid
import pytest
from PIL import Image,ImageDraw,ImageChops
from backend.services.reel_creator.models import Composition,Photo,RenderInput
from backend.services.reel_creator.service import ReelCreator
from backend.services.reel_creator.flexible_scene import photo_geometry, layers, movement_for
from backend.services.reel_creator.daily_brief import runtime_status
from backend.services.reel_creator.content_fit import check_content_fit


def story(tmp_path,template='lens_report',ratio='4:5',large=False):
    creator=ReelCreator(tmp_path/'creator',tmp_path/'media',admission=lambda:None);photos=[]
    for i,color in enumerate(['#d34c35','#257db0']):
        size=(1600,2400) if large else (600,900)
        image=Image.new('RGB',size,color);draw=ImageDraw.Draw(image)
        for x in range(0,size[0],size[0]//12):draw.rectangle((x,0,x+size[0]//24,size[1]),fill='#f2d492')
        draw.ellipse((size[0]*.25,size[1]*.25,size[0]*.7,size[1]*.6),fill='#153546')
        data=io.BytesIO();image.save(data,'PNG');image.close()
        asset=creator.import_asset(data.getvalue(),f'fixture-{i}.png','image')
        photos.append(Photo(image_id=asset['id'],headline=f'Community spaces reopen',caption='A fictional demonstration.',framing='fit',movement='pan_left',zoom=1.2,offset_x=.05,offset_y=.02))
    spec=Composition(version=3 if template=='lens_report' else 2,layout_revision=2 if template=='lens_report' else 1,
        template=template,aspect_ratio=ratio,photos=photos,image_id=photos[0].image_id,duration=10,fps=24,
        source='Fictional demonstration',brand='LENS REPORT' if template=='lens_report' else 'DAILY BRIEF',category='COMMUNITY',
        supporting='A fictional demonstration.',photo_layout='full_bleed',lens_theme='dark',motion_intensity='dramatic',accent='#e02f2f')
    return creator,spec


@pytest.mark.parametrize('template',['lens_report','daily_brief'])
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_adjustable_photo_geometry_and_text_layers(tmp_path,template,ratio):
    creator,spec=story(tmp_path,template,ratio)
    height=1920 if ratio=='9:16' else 1350
    viewport,bounds=photo_geometry(spec,spec.photos[0],(600,900))
    assert viewport==(0,0,1080,height)
    assert bounds[0]>-300 and bounds[2]>=1080
    assert movement_for(spec,spec.photos[0])=='pan_left'
    assert movement_for(spec.model_copy(update={'motion':False}),spec.photos[0])=='still'
    assert movement_for(spec,spec.photos[0].model_copy(update={'movement':'still'}))=='still'
    adjusted=spec.model_copy(update={'photo_layout':'adjustable','photo_panel_end':.5})
    area,_=photo_geometry(adjusted,spec.photos[0],(600,900))
    assert area[1]+area[3]==round(height*.5)
    assert check_content_fit(spec)['fits']
    sprites=[];prepared=layers(spec,spec.photos[0],creator.asset_path(spec.image_id,'image'),text_sprites=sprites)
    assert prepared[0].size==(1080,height)
    assert {s['kind'] for s in sprites}=={'title','body'}
    assert prepared[1].getpixel((300,round(height*.3)))[3] < 255
    for layer in prepared:layer.close()
    for sprite in sprites:sprite['image'].close()


@pytest.mark.parametrize('template',['lens_report','daily_brief'])
def test_native_manifest_preserves_manual_framing_and_separate_planes(tmp_path,template):
    creator,spec=story(tmp_path,template)
    if template=='lens_report':from backend.services.reel_creator.lens_report import prepare
    else:from backend.services.reel_creator.daily_brief import prepare
    directory=tmp_path/'prepared';directory.mkdir()
    command=prepare(spec,[creator.asset_path(p.image_id,'image') for p in spec.photos],None,directory,preview=True)
    data=json.loads(__import__('pathlib').Path(command[2]).read_text())
    assert data['flexibleScene'] and len(data['photoLayers'])==2
    assert data['movements']==['pan_left','pan_left']
    assert data['transitionFrames']>0 and data['textSprites'][0]
    assert (directory/data['photoLayers'][0]['file']).is_file()
    assert data['photoLayers'][0]['bounds']['width']>=360


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'),reason='Prepared Remotion runtime required')
@pytest.mark.parametrize('template',['lens_report','daily_brief'])
@pytest.mark.parametrize('ratio',['9:16','4:5'])
def test_real_photo_motion_and_text_reveal_with_manual_framing(tmp_path,template,ratio):
    creator,spec=story(tmp_path,template,ratio)
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec,kind='preview'))
    creator._render(job);result=creator.store.job(job['id'])
    assert result['state']=='completed',result.get('error')
    path=creator.output_path(result);frames=[]
    for at in [0,.35,1.8,3.3]:
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
        frames.append(Image.open(io.BytesIO(raw)).convert('RGB'))
    w,h=frames[0].size
    area=(30,round(h*.20),w-30,round(h*.45))
    diff=ImageChops.difference(frames[2].crop(area),frames[3].crop(area))
    assert sum(max(pixel)>15 for pixel in diff.get_flattened_data())>1000,'Selected pan did not visibly move the manually positioned photo'
    title_top=1180 if ratio=='9:16' else 740
    title=(24,round((title_top+40)/3),336,round((title_top+300)/3))
    assert ImageChops.difference(frames[0].crop(title),frames[1].crop(title)).getbbox()
    sprites=[];prepared=layers(spec,spec.photos[0],creator.asset_path(spec.image_id,'image'),text_sprites=sprites)
    for layer in prepared:layer.close()
    body=next(sprite for sprite in sprites if sprite['kind']=='body')
    y=round(body['y']/3);height=max(1,round(body['image'].height/3))
    crop=frames[2].crop((round(body['x']/3),y,round((body['x']+body['image'].width)/3),y+height))
    rows=sum(any(min(crop.getpixel((x,row)))>110 and max(crop.getpixel((x,row)))-min(crop.getpixel((x,row)))<25 for x in range(crop.width)) for row in range(crop.height))
    assert rows>=height*.65,'Small subtitle glyphs were clipped in the video'
    for sprite in sprites:sprite['image'].close()
    assert result.get('peak_rss_bytes',0)<768*1024*1024
    output=__import__('pathlib').Path('tools/news-reel/output/flexible-review');output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(path,output/f'{template}-{ratio.replace(":","x")}.mp4')


@pytest.mark.skipif(not runtime_status()[0] or not shutil.which('ffmpeg'),reason='Prepared Remotion runtime required')
def test_native_photo_full_resolution_export_is_bounded(tmp_path):
    creator,spec=story(tmp_path,'daily_brief','9:16',large=True)
    spec=spec.model_copy(update={'resolution':1080,'fps':30,'audio_preset':'headlines'})
    job=creator.enqueue(RenderInput(request_id=uuid.uuid4().hex,composition=spec))
    creator._render(job);result=creator.store.job(job['id'])
    assert result['state']=='completed',result.get('error')
    assert result.get('peak_rss_bytes',0)<768*1024*1024
    print(f"Native 1080 photo scene peak: {result.get('peak_rss_bytes',0)/1024/1024:.1f} MiB")
