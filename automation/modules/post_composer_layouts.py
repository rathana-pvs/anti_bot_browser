"""Visual layout handlers for photo/text composers, independent of Post/Next flow.

Add a handler to POST_COMPOSER_LAYOUTS when Facebook introduces another layout.
Each handler owns its OCR region and structural guards; none uses blind clicks.
"""

from dataclasses import dataclass

from engine.text_matcher import OcrTextMatcher


@dataclass(frozen=True)
class CaptionMatch:
    layout_id: str
    target: tuple[int, int]
    region: str
    confidence: float | None


class CenteredDialogLayout:
    layout_id = "centered_dialog"
    region_name = "composer_modal"

    def locate(self, screen, vision, caption_matches, phrase_matches):
        region = vision.get_pixel_region(screen.shape, self.region_name)
        items = vision.read_text(screen=screen, region=region, min_confidence=.20)
        titles = [i for i in items if len(OcrTextMatcher.normalize(i['text']).split()) <= 4
                  and phrase_matches('Create post', i['text'])]
        prompts = [i for i in items if caption_matches(i['text'])
                   or phrase_matches('Write something', i['text'])
                   or phrase_matches('Create a public post', i['text'])]
        targets = [p for p in prompts if any(t['center'][1] < p['center'][1] for t in titles)]
        if len(targets) != 1:
            return None
        item = targets[0]
        return CaptionMatch(self.layout_id, item['center'], self.region_name, item.get('confidence'))


class LeftEditorPreviewLayout:
    layout_id = "left_editor_preview"
    region_name = "post_left_editor"
    control_labels = ('people', 'location', 'fundraiser', 'collaborator', 'feeling activity')

    def locate(self, screen, vision, caption_matches, phrase_matches):
        height, width = screen.shape[:2]
        region = (0, int(height * .13), int(width * .38), int(height * .32))
        items = vision.read_text(screen=screen, region=region, min_confidence=.20)
        titles = [i for i in items if phrase_matches('Create post', i['text'])]
        prompts = [i for i in items if caption_matches(i['text'])]
        media = [i for i in items if phrase_matches('Uploaded media', i['text'])]
        controls = []
        for item in items:
            normalized = OcrTextMatcher.normalize(item['text'])
            for label in self.control_labels:
                # An icon can be OCR'd as a prefix ("e+ Collaborator").
                # Short labels remain exact; do not relax publish recognition.
                if normalized == label or normalized.endswith(' ' + label):
                    controls.append((label, item))
                    break
        targets = []
        for prompt in prompts:
            px, py = prompt['center']
            for title in titles:
                tx, ty = title['center']
                if (0 <= tx < region[2] and 0 <= px < region[2]
                        and 20 < py - ty < height * .22
                        and len({label for label, i in controls
                                 if 0 <= i['center'][0] < region[2]
                                 and ty < i['center'][1] < py}) >= 2
                        and any(0 <= i['center'][0] < region[2]
                                and 0 < i['center'][1] - py < height * .18 for i in media)):
                    targets.append(prompt)
                    break
        if len(targets) != 1:
            return None
        item = targets[0]
        return CaptionMatch(self.layout_id, item['center'], self.region_name, item.get('confidence'))


POST_COMPOSER_LAYOUTS = (CenteredDialogLayout(), LeftEditorPreviewLayout())


class PostComposerLayouts:
    def __init__(self, vision, caption_matches, phrase_matches, layouts=POST_COMPOSER_LAYOUTS):
        self.vision = vision
        self.caption_matches = caption_matches
        self.phrase_matches = phrase_matches
        self.layouts = layouts

    def detect(self, screen):
        for layout in self.layouts:
            match = layout.locate(screen, self.vision, self.caption_matches, self.phrase_matches)
            if match:
                return match
        return None
