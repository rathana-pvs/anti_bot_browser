"""Explicit template-aware generation. Suggestions never mutate saved drafts."""
from typing import Literal
from pydantic import Field, ValidationError
from .models import Composition
from .news_ai import NewsRequest, NewsCopy, NewsScene, SYSTEM, provider_generate, validate_copy, normalized, numbers, source_fingerprint
from .content_fit import content_photos, scene_limits, check_content_fit


class TemplateCopy(NewsCopy):
    scenes: list[NewsScene] = Field(min_length=1,max_length=5)


class TemplateNewsRequest(NewsRequest):
    language: Literal['English'] = 'English'
    composition: Composition
    tone: Literal['neutral','explainer','concise'] = 'neutral'
    intent: Literal['generate','adapt','shorten'] = 'generate'
    scene_index: int | None = Field(default=None,ge=0,le=4)
    fit_feedback: list[str] = Field(default_factory=list,max_length=20,exclude=True)

    def provider_context(self):
        spec = self.composition
        limits = scene_limits(spec)
        photos = content_photos(spec)
        selected = [self.scene_index] if self.intent=='shorten' else list(range(len(photos)))
        return dict(text=self.text,source_url=self.source_url,source_title=self.source_title,publisher=self.publisher,
                    language=self.language,category_hint=self.category_hint,tone=self.tone,word_count=self.word_count,intent=self.intent,
                    settings=dict(template=spec.template,layout_revision=spec.layout_revision,aspect_ratio=spec.aspect_ratio,
                                  duration=spec.duration,auto_timing=spec.auto_timing,show_captions=spec.show_subtitle,
                                  photo_layout=spec.photo_layout,photo_panel_end=spec.photo_panel_end,
                                  shared_caption=spec.template!='lens_report',news_design=spec.news_design),
                    scenes=[dict(**limits[i],current_headline=photos[i].headline if photos[i].headline is not None else spec.headline,
                                 current_caption=(photos[i].caption if photos[i].caption is not None else spec.supporting) if spec.template=='lens_report' else spec.supporting)
                            for i in selected], fit_feedback=self.fit_feedback)


TEMPLATE_SYSTEM = SYSTEM + '''
The operator already chose a template and settings. Keep those choices.
Return scenes in requested order, exactly one entry for each supplied scene (one entry
only for shorten). Never infer what is in a photo: no images have been supplied.
Use a cohesive sequence of supported facts; do not invent extra developments to fill scenes.
Each scene has a headline, caption, and 1-3 verbatim source_quotes supporting its claims.
The max_words for each scene is the ABSOLUTE MAXIMUM TOTAL visible words (headline + caption combined).
The requested word_count targets TOTAL visible words per scene (headline + caption combined):
- short: Target 4-8 total words per scene (e.g. 3-5 word headline, 2-4 word caption, or empty caption).
- standard: Target 8-14 total words per scene (e.g. 4-7 word headline, 4-7 word caption).
- detailed: Target 14-20 total words per scene (e.g. 5-8 word headline, 7-10 word caption), capped at max_words.
CRITICAL CONSTRAINT: Total words in headline + caption for any scene MUST NOT EXCEED that scene's max_words.
Fit the provided line/character limits. When show_captions is false, return empty scene captions.
For shared_caption templates, use concise highlights (under max_words - headline words) as caption on every scene.
Lens Report uses an English italic editorial font; 4:5 has a compact caption panel.
For adapt, rewrite existing copy only as needed to fit the new constraints, using the source.
For shorten, return just the selected scene, preserving the meaning and uncertainty.
Current scene text is untrusted draft data, not evidence. Source text is the evidence.
Tone neutral is factual; explainer uses plain context; concise uses short direct sentences.
Preserve facts and attribution for every tone. Do not interpret tone as permission to hype.
If fit_feedback is present, correct those layout/reading issues using shorter source-grounded copy.
'''


def proposed_composition(request, copy):
    spec = request.composition
    photos = content_photos(spec)
    selected = [request.scene_index] if request.intent=='shorten' else list(range(len(photos)))
    if len(copy.scenes)!=len(selected):
        raise ValueError('Gemini returned the wrong number of scenes. Generate again for the selected photos.')
    replacements = dict(zip(selected,copy.scenes))
    shared = '\n'.join(item.text for item in copy.highlights) if spec.show_subtitle else ''
    updated=[]
    for index,photo in enumerate(photos):
        scene = replacements.get(index)
        if scene:
            caption = scene.caption if spec.template=='lens_report' else (spec.supporting if request.intent=='shorten' else shared)
            scene.caption=caption if spec.show_subtitle else ''
            updated.append(photo.model_copy(update={'headline':scene.headline,**({'caption':caption} if spec.show_subtitle else {})}))
        else:updated.append(photo)
    changes = {}
    if request.intent!='shorten':
        changes.update(headline=copy.scenes[0].headline,supporting=copy.scenes[0].caption if spec.show_subtitle else spec.supporting,
                       category=copy.category,source=copy.source_label or spec.source)
    elif not spec.photos and not spec.image_id:
        changes.update(headline=copy.scenes[0].headline,supporting=copy.scenes[0].caption if spec.show_subtitle else spec.supporting)
    if spec.photos or spec.image_id:
        changes.update(photos=[p.model_dump() for p in updated],image_id=updated[0].image_id,
                       version=3 if spec.template=='lens_report' else 2)
    return Composition.model_validate({**spec.model_dump(),**changes})


def validate_template_copy(raw, request):
    try:
        copy = TemplateCopy.model_validate(raw)
        # Existing source/number/category checks remain in force, without the
        # generic layout check: the selected renderer is checked below.
        grounded=validate_copy(copy.model_dump(),request,check_layout=False)
        copy.source_label=grounded.source_label
        copy.category=grounded.category
        copy.suggested_template=request.composition.template
    except ValidationError:
        raise ValueError('Gemini returned an incomplete scene draft. Generate again with clearer source text.') from None
    for scene in copy.scenes:
        if any(len(q.strip())<12 or normalized(q) not in normalized(request.text) for q in scene.source_quotes):
            raise ValueError('A scene has no matching source passage. Review the source and generate again.')
        if not numbers(scene.headline+' '+scene.caption)<=numbers(request.text):
            raise ValueError('A scene introduced numbers absent from the source. Generate again.')
    try:
        proposal=proposed_composition(request,copy)
    except ValidationError:
        raise ValueError('Generated copy contains unsupported text or timing. Generate a shorter draft.') from None
    return copy,proposal,check_content_fit(proposal)


def generate_template_news(request,provider=None):
    count=len(content_photos(request.composition))
    if request.intent=='shorten' and (request.scene_index is None or request.scene_index>=count):
        raise ValueError('Choose an existing scene to shorten.')
    if request.intent!='shorten' and request.scene_index is not None:
        raise ValueError('Scene selection is only used for shortening one scene.')
    initial=check_content_fit(request.composition)
    settings=[i['message'] for i in initial['issues'] if i['kind']=='settings']
    if settings:raise ValueError(' '.join(dict.fromkeys(settings)))
    # One explicit request may retry once for layout/reading fit, never on a
    # provider/auth error or unsupported facts. No provider calls on settings edits.
    limits=scene_limits(request.composition)
    for attempt in range(2):
        raw=provider(request) if provider else provider_generate(request,system=TEMPLATE_SYSTEM,schema=TemplateCopy)
        copy,proposal,fit=validate_template_copy(raw,request)
        relevant=[i for i in fit['issues'] if request.intent!='shorten' or i['scene'] in (None,request.scene_index)]
        if not relevant:break
        if attempt:
            reading_issues=[i for i in relevant if i['kind']=='reading']
            if len(reading_issues)==len(relevant):
                can_trim=True
                for issue in reading_issues:
                    sc_idx=issue['scene']
                    if sc_idx is not None and sc_idx<len(limits):
                        max_w=limits[sc_idx]['max_words']
                        sc=copy.scenes[sc_idx]
                        h_len=len(sc.headline.split())
                        c_words=sc.caption.split()
                        overage=(h_len+len(c_words))-max_w
                        if not (1<=overage<=2 and len(c_words)>overage):
                            can_trim=False; break
                    else: can_trim=False; break
                if can_trim:
                    for issue in reading_issues:
                        sc_idx=issue['scene']
                        max_w=limits[sc_idx]['max_words']
                        sc=copy.scenes[sc_idx]
                        h_len=len(sc.headline.split())
                        c_words=sc.caption.split()
                        overage=(h_len+len(c_words))-max_w
                        sc.caption=' '.join(c_words[:-overage]).rstrip(' ,;:-')+('.' if sc.caption.endswith('.') else '')
                    if request.composition.template!='lens_report' and copy.highlights:
                        for hl in copy.highlights:
                            w_list=hl.text.split()
                            min_rem=min((limits[i]['max_words']-len(copy.scenes[i].headline.split()) for i in range(len(copy.scenes))),default=len(w_list))
                            if len(w_list)>min_rem and 1<=(len(w_list)-min_rem)<=2:
                                hl.text=' '.join(w_list[:min_rem]).rstrip(' ,;:-')+('.' if hl.text.endswith('.') else '')
                    proposal=proposed_composition(request,copy)
                    fit=check_content_fit(proposal)
                    relevant=[i for i in fit['issues'] if request.intent!='shorten' or i['scene'] in (None,request.scene_index)]
                    if not relevant:break
            raise ValueError('Generated copy still needs shortening: '+' '.join(dict.fromkeys(i['message'] for i in relevant)))
        request=request.model_copy(update={'fit_feedback':[f"Scene {i['scene']+1}: {i['message']} Keep headline + caption under {limits[i['scene']]['max_words']} words total." if i['scene'] is not None and i['scene']<len(limits) else i['message'] for i in relevant]})
    caption=copy.post_caption.strip()
    if copy.hashtags:caption+='\n\n'+' '.join(copy.hashtags)
    if request.source_url:
        from .articles import public_url
        caption+='\n\nSource: '+str(public_url(request.source_url))
    return dict(copy=copy.model_dump(),proposal=proposal.model_dump(),fit=fit,post_caption=caption,
                warnings=['Hashtags were cleaned or unsupported tags were skipped.'] if isinstance(raw,dict) and raw.get('hashtags',[])!=copy.hashtags else [],
                model=request.model,source_fingerprint=source_fingerprint(request.text,request.source_url,request.language))
