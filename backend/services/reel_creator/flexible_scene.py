"""Adjustable photo geometry and editable scene planes for local Remotion layouts."""
from PIL import Image, ImageDraw, ImageOps, ImageFilter, ImageColor
from .composition import fit_text, draw_lines
from .framing import crop_image, transformed_photo
from .layout import canvas_size, output_size


def enabled(spec):
    return spec.template in ('lens_report','daily_brief') and spec.photo_layout != 'template'


def panel_top(spec):
    _,height=canvas_size(spec)
    return round(height*spec.photo_panel_end) if spec.photo_panel_end is not None else 1180 if spec.aspect_ratio=='9:16' else 740


def header_height(spec):
    return 210 if spec.aspect_ratio=='9:16' else 128 if spec.brand or spec.logo_id else 96


def photo_geometry(spec,photo,image_size):
    width,height=canvas_size(spec)
    viewport=(0,0,width,height) if spec.photo_layout=='full_bleed' else (0,header_height(spec),width,panel_top(spec)-header_height(spec))
    left,top,area_width,area_height=viewport
    iw,ih=image_size
    treatment=photo.framing if photo.framing!='inherit' else spec.treatment
    factor=(min if treatment=='fit' else max)(area_width/iw,area_height/ih)*photo.zoom
    w,h=iw*factor,ih*factor
    x=left+(area_width-w)*(.5 if treatment=='fit' else photo.focal_x)+photo.offset_x*width
    y=top+(area_height-h)*(.5 if treatment=='fit' else photo.focal_y)+photo.offset_y*height
    return viewport,(x,y,w,h)


def movement_for(spec,photo):
    if not spec.motion or photo.movement=='still':return 'still'
    movement=photo.movement if photo.movement!='auto' else spec.slideshow_effect
    return 'zoom_in' if movement=='auto' else movement if movement in ('zoom_in','zoom_out','pan_left','pan_right') else 'still'


def backdrop(image,size):
    return ImageOps.fit(image,(270,round(size[1]/4))).filter(ImageFilter.GaussianBlur(16)).resize(size).convert('RGBA')


def fit_scene_text(spec,title,body):
    if spec.template=='lens_report':
        from .lens_report import fit_lens_text
        lines,font,leading,body_lines,body_size,baseline=fit_lens_text(spec,title,body)
        return lines,font.size,leading,body_lines,body_size,baseline,font
    _,height=canvas_size(spec)
    available=height-180-20-panel_top(spec)-42
    body_lines,body_size=fit_text(body,912,3 if spec.aspect_ratio=='9:16' else 2,34,26,False) if body else ([],34)
    body_height=len(body_lines)*round(body_size*1.55)
    for size in range(78,47,-2):
        try:lines,_=fit_text(title,912,3,size,size) if title else ([],size)
        except ValueError:continue
        leading=round(size*1.55)
        if len(lines)*leading+(30+body_height if body else 0)<=available:
            return lines,size,leading,body_lines,body_size,panel_top(spec)+42+size,None
    raise ValueError('Shorten the Daily Brief headline/caption or reduce the photo area height.')


def layers(spec,photo,path,logo=None,text_sprites=None):
    size=canvas_size(spec);width,height=size
    with Image.open(path) as raw:image=crop_image(raw.convert('RGB'),photo.crop)
    _,bounds=photo_geometry(spec,photo,image.size)
    picture=backdrop(image,size)
    foreground=transformed_photo(image,bounds,size)
    if spec.photo_layout=='adjustable':
        clip=Image.new('RGBA',size);start=header_height(spec);end=panel_top(spec)
        clip.alpha_composite(foreground.crop((0,start,width,end)),(0,start));foreground.close();foreground=clip
    picture.alpha_composite(foreground);foreground.close();image.close()
    overlay=Image.new('RGBA',size);caption=Image.new('RGBA',size);brand=Image.new('RGBA',size)
    paper='#000000' if spec.lens_theme=='dark' else '#f7f5ef'
    ink='#f7f5ef' if spec.lens_theme=='dark' else '#101318'
    muted='#b8bbc2' if spec.lens_theme=='dark' else '#62666d'
    border='#34383f' if spec.lens_theme=='dark' else '#d6d3cb'
    od=ImageDraw.Draw(overlay);head=header_height(spec);end=panel_top(spec);rgb=ImageColor.getrgb(paper)
    if spec.photo_layout=='full_bleed':
        for y in range(height):
            top=round(235*max(0,1-y/(head+180)))
            bottom=round(248*max(0,min(1,(y-(end-240))/340)))
            od.line((0,y,width,y),fill=(*rgb,max(top,bottom)))
    else:
        od.rectangle((0,0,width,head),fill=paper);od.rectangle((0,end,width,height),fill=paper)
    title=photo.headline if photo.headline is not None else spec.headline
    body=(photo.caption if photo.caption is not None else spec.supporting) if spec.template=='lens_report' else spec.supporting
    body=body if spec.show_subtitle else ''
    lines,title_size,leading,body_lines,body_size,baseline,font=fit_scene_text(spec,title,body)
    def collect(text,kind,index,y):
        layer=Image.new('RGBA',size);draw=ImageDraw.Draw(layer)
        if kind=='title' and font:draw.text((72,y),text,font=font,fill=ink,anchor='ls')
        else:draw_lines(draw,(72,y),[text],title_size if kind=='title' else body_size,ink if kind=='title' else muted,kind=='title')
        caption.alpha_composite(layer)
        bbox=layer.getbbox()
        if text_sprites is not None and bbox:text_sprites.append(dict(image=layer.crop(bbox),x=bbox[0],y=bbox[1],kind=kind,index=index))
        layer.close()
    for index,line in enumerate(lines):collect(line,'title',index,baseline);baseline+=leading
    baseline+=20
    for index,line in enumerate(body_lines):collect(line,'body',index,baseline);baseline+=round(body_size*1.55)
    bd=ImageDraw.Draw(brand);brand_x=72;compact=spec.aspect_ratio=='4:5'
    if logo:
        with Image.open(logo) as raw:mark=ImageOps.contain(raw.convert('RGBA'),(48,48) if compact else (64,64))
        brand.alpha_composite(mark,(72,14 if compact else 75));mark.close();brand_x=138 if compact else 158
    for value,xy,preferred,minimum,color,count in (
        (spec.brand,(brand_x,60 if compact else 121),28,22,ink,1),
        (spec.category,(72,head-30),22,18,spec.accent,1),
        (spec.source,(72,height-126),26,22,muted,2)):
        if value:
            fitted,fs=fit_text(value,1008-xy[0],count,preferred,minimum,False)
            draw_lines(bd,xy,fitted,fs,color,False)
    bd.line((72,head-14,1008,head-14),fill=border,width=2)
    bd.line((72,height-180,1008,height-180),fill=border,width=2)
    if spec.template=='daily_brief':bd.rectangle((72,end+20,144,end+25),fill=spec.accent)
    if spec.show_progress:od.rectangle((0,height-6,width,height),fill=border)
    return picture,overlay,caption,brand


def prepare_photo(spec,photo,path,directory,index,preview=False):
    size=canvas_size(spec);output=output_size(spec,preview);scale=output[0]/size[0]
    with Image.open(path) as raw:image=crop_image(raw.convert('RGB'),photo.crop)
    viewport,bounds=photo_geometry(spec,photo,image.size)
    background=backdrop(image,size);background_name=f'native-{index}-backdrop.png'
    background.resize(output,Image.Resampling.LANCZOS).save(directory/background_name);background.close()
    image.thumbnail((720,1280) if preview else (2160,3840),Image.Resampling.LANCZOS)
    name=f'native-{index}-photo.png';image.save(directory/name);image.close()
    keys=('x','y','width','height')
    return dict(file=name,background=background_name,viewport=dict(zip(keys,[v*scale for v in viewport])),bounds=dict(zip(keys,[v*scale for v in bounds])))


def save_sprites(sprites,spec,directory,index,preview=False):
    scale=output_size(spec,preview)[0]/1080;records=[]
    for i,sprite in enumerate(sprites):
        name=f'scene-{index}-text-{i}.png';picture=sprite['image']
        size=(max(1,round(picture.width*scale)),max(1,round(picture.height*scale)))
        picture.resize(size,Image.Resampling.LANCZOS).save(directory/name);picture.close()
        records.append(dict(file=name,x=round(sprite['x']*scale),y=round(sprite['y']*scale),width=size[0],height=size[1],kind=sprite['kind'],index=sprite['index']))
    return records
