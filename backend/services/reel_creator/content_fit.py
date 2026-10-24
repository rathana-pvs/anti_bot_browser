"""Offline content constraints shared by generation, review, and renderer checks."""
import math
from .composition import fit_news_text, fit_text
from .models import Photo
from .story import photos_for, timeline


def content_photos(spec):
    return photos_for(spec) or [Photo(image_id='0'*32)]


def scene_limits(spec):
    photos = content_photos(spec)
    timed = spec if photos_for(spec) else spec.model_copy(update={'photos': photos})
    holds = {i: [] for i in range(len(photos))}
    for scene in timeline(timed):
        holds[scene['photo_index']].append(scene['hold']/spec.fps)
    entrance = .6 if spec.motion and spec.text_animation != 'none' else 0
    return [dict(scene=i, seconds=min(holds[i]) if holds[i] else 0,
                 max_words=max(0, math.floor(((min(holds[i]) if holds[i] else 0)-entrance)*3)),
                 headline_max_chars=140, caption_max_chars=200,
                 headline_max_lines=3, caption_max_lines=3 if spec.template=='daily_brief' or spec.template=='lens_report' and spec.aspect_ratio=='9:16' else 2)
            for i in range(len(photos))]


def check_content_fit(spec):
    issues = []
    limits = scene_limits(spec)
    from .flexible_scene import enabled, fit_scene_text
    if spec.template == 'daily_brief' and spec.aspect_ratio != '9:16' and not (enabled(spec) and spec.aspect_ratio=='4:5'):
        issues.append(dict(scene=None,kind='settings',message='Daily Brief supports 9:16. Choose 9:16 or another template.'))
    if spec.template == 'lens_report' and spec.layout_revision != 2:
        issues.append(dict(scene=None,kind='settings',message='Enable animated Lens Report to generate for its current layout.'))
    if spec.template == 'daily_brief' and not enabled(spec):
        try:
            if spec.category:fit_text(spec.category,266,1,22,16)
        except ValueError:
            issues.append(dict(scene=None,kind='layout',message='Shorten the category label for the Daily Brief header.'))
    for index,photo in enumerate(content_photos(spec)):
        title = photo.headline if photo.headline is not None else spec.headline
        body = (photo.caption if photo.caption is not None else spec.supporting) if spec.template=='lens_report' else spec.supporting
        body = body if spec.show_subtitle else ''
        if not limits[index]['seconds']:
            issues.append(dict(scene=index,kind='settings',message='This photo is outside the current video duration. Increase duration or remove it.'))
        try:
            if spec.template == 'lens_report':
                from .lens_report import fit_lens_text
                fit_lens_text(spec,title,body)
            elif spec.template == 'daily_brief' and enabled(spec):
                fit_scene_text(spec,title,body)
            elif spec.template == 'daily_brief':
                lines,size = fit_text(title,912,3,78,48) if title else ([],78)
                body_lines,body_size = fit_text(body,912,3,42,28,False) if body else ([],42)
                last = min(1110+len(lines)*round(size*1.55)+20,1430)+65
                if body_lines and last+(len(body_lines)-1)*round(body_size*1.55)>1650:
                    raise ValueError('Headline and caption fill too much of the Daily Brief panel.')
            else:
                fit_news_text(spec.model_copy(update={'headline':title,'supporting':body}))
        except ValueError as error:
            issues.append(dict(scene=index,kind='layout',message=str(error)))
        words = len((title+' '+body).split())
        entrance=.6 if spec.motion and spec.text_animation!='none' else 0
        limits[index].update(words=words,recommended_seconds=max(3,math.ceil(words/3+entrance)))
        if words > limits[index]['max_words']:
            issues.append(dict(scene=index,kind='reading',message=f"{words} words need more reading time; aim for at most {limits[index]['max_words']} words in {limits[index]['seconds']:g} seconds."))
    return dict(fits=not issues,issues=issues,scenes=limits)
