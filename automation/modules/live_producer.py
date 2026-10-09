"""Secret-free Live Producer recognition using shared declarative templates.

OCR stays in memory. Only template IDs, workflow metadata and state names may
leave this module. Producer keys and incoming video are never evidence.
"""
from dataclasses import dataclass
import json
import re

from composer_templates import ComposerTemplateDetector, ComposerTemplateRegistry, TemplateObservation


def label(item):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', item['text'].lower())).strip()


def phrase(items, text):
    """Match one label or adjacent lines in the same small control region."""
    if any(text in label(i) for i in items):
        return True
    ordered = sorted(items, key=lambda i: (i['center'][1], i['center'][0]))
    for a, b in zip(ordered, ordered[1:]):
        ah = a['bounds'][3] - a['bounds'][1]
        bh = b['bounds'][3] - b['bounds'][1]
        gap = b['bounds'][1] - a['bounds'][3]
        if 0 <= gap <= max(ah, bh) and abs(a['bounds'][0] - b['bounds'][0]) < 30:
            if text in label(a) + ' ' + label(b):
                return True
    return False


def enabled_color(screen, item, color='blue'):
    x1, y1, x2, y2 = item['bounds']
    crop = screen[max(0, y1-12):min(screen.shape[0], y2+12), max(0, x1-30):min(screen.shape[1], x2+30)]
    if not crop.size:
        return False
    b, g, r = (crop[:, :, n].astype(float) for n in range(3))
    mask = (b > 140) & (b > r + 65) & (b > g + 25) if color == 'blue' else (r > 170) & (r > b + 35) & (r > g + 35)
    return float(mask.mean()) > .20


@dataclass
class ProducerFrame:
    image: object
    regions: dict
    signals: dict
    template_id: str | None


class LiveProducerTemplates:
    def __init__(self, package):
        self.templates = ComposerTemplateRegistry(package.root).load('live')
        self.config = json.loads((package.root / 'config' / 'producer.json').read_text())
        if self.config.get('schema_version') != 1:
            raise ValueError('Unsupported Live Producer configuration')
        for region in self.config['regions'].values():
            if len(region) != 4 or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1 for v in region):
                raise ValueError('Invalid Live Producer OCR region')
            if region[2] <= 0 or region[3] <= 0 or region[0] + region[2] > 1.00001 or region[1] + region[3] > 1.00001:
                raise ValueError('Live Producer OCR region escapes the screen')
        # Layout data cannot expand publication controls into the incoming video.
        for name in ('sidebar', 'footer'):
            x, y, width, height = self.config['regions'][name]
            if x != 0 or width > .22 or (name == 'footer' and y < .85):
                raise ValueError('Live control regions must remain in the left sidebar')
        for action, seconds in self.config['timeouts'].items():
            if action not in {'prepare', 'start', 'end'} or not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 150:
                raise ValueError('Invalid Live Producer timeout')
        self.detector = ComposerTemplateDetector(stable_observations=1)

    def labels(self, target):
        return set(self.config['targets'][target])

    def has(self, region, signal):
        return any(phrase(region, p) for p in self.config['phrases'][signal])

    def controls(self, frame, target):
        items = frame.regions.get('footer', [])
        return [i for i in items if label(i).replace('iive', 'live') in self.labels(target) and i.get('confidence', 0) >= .55]

    def observe(self, image, regions):
        sidebar, footer = regions.get('sidebar', []), regions.get('footer', [])
        signal = {}
        for name in ('active_surface', 'live_notice', 'ended_surface'):
            signal[name] = float(self.has(sidebar, name))
        signal['ended_control'] = float(self.has(footer, 'ended_control'))
        frame = ProducerFrame(image, regions, signal, None)
        starts, ends = self.controls(frame, 'go_live'), self.controls(frame, 'end_live')
        signal['go_live_enabled'] = float(len(starts) == 1 and enabled_color(image, starts[0]))
        signal['end_control'] = float(len(ends) == 1 and enabled_color(image, ends[0], 'red'))
        for name in ('left', 'center'):
            items = regions.get('setup_' + name, [])
            signal[name + '_setup'] = float(self.has(items, 'setup_heading') and self.has(items, 'stream_key'))
        signal['setup_panel'] = max(signal['left_setup'], signal['center_setup'])
        signal['details_saved'] = float(self.has(regions.get('details', []), 'details_saved'))
        # Facebook only enables this footer action after source and required
        # details are ready. Avoid OCRing the video or stream-key panel at start.
        if signal['go_live_enabled'] and phrase(sidebar, 'create live video'):
            signal['setup_panel'] = signal['details_saved'] = 1.0
        source = regions.get('setup_left', []) + regions.get('setup_center', [])
        signal['source_heading'] = float(self.has(source, 'source_heading'))
        signal['source_option'] = float(any(label(i) in self.labels('streaming_source') for i in source))
        home = regions.get('home', [])
        signal['setup_control'] = float(any(label(i) in self.labels('open_setup') for i in home))
        # A scrolled Home page can hide its Welcome heading above the OCR region.
        # Require the fixed Home sidebar and both history tabs beside Set up.
        scrolled_home = (signal['setup_control'] and phrase(home, 'past live videos')
                         and phrase(home, 'live now')
                         and any(label(i) == 'home' and i.get('confidence', 0) >= .8 for i in sidebar))
        signal['home_surface'] = float(self.has(home, 'home_surface') or scrolled_home)
        result = self.detector.detect(self.templates.values(), [TemplateObservation(signal)])
        frame.template_id = result.template.template_id if result.outcome == 'selected' else None
        return frame

    def permits(self, frame, capability, target=None, state=None):
        if frame.template_id is None:
            return False
        return any(step['capability'] == capability and (target is None or step.get('target') == target)
                   and (state is None or step.get('state') == state)
                   for step in self.templates[frame.template_id].steps)
