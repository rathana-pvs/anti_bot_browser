"""Fill Live's reversible post details using fresh OCR and X11 input.

No screenshots, field contents or clipboard values are written to evidence.
This module never clicks Go live or starts ingest.
"""
import time

from engine.text_matcher import OcrTextMatcher
from modules.live_producer import label, phrase, enabled_color


class LivePostDetails:
    OPEN = {'whats your live video about', 'create post', 'edit post details'}
    TITLE = {'title', 'title required', 'add a title', 'add a title required'}
    CAPTION = {'description', 'description required', 'say something about this live video',
               'say something about your live video', 'add a description'}
    STABLE_OBSERVATIONS = 2
    MAX_FIELD_OBSERVATIONS = 8
    TEXT_THRESHOLD = .72
    AMBIGUITY_MARGIN = .08

    def __init__(self, task):
        self.task = task
        self.matcher = OcrTextMatcher(default_threshold=self.TEXT_THRESHOLD, min_fuzzy_characters=5)

    def dialog_region(self, image):
        h, w = image.shape[:2]
        x, y, rw, rh = self.task.layouts.config['regions'].get(
            'post_details_dialog', (.35, .25, .30, .60))
        return int(x*w), int(y*h), int(rw*w), int(rh*h)

    def read_dialog(self):
        image = self.task.client.screenshot()
        # The post form is narrower than the End Live confirmation region.
        # Keep background setup labels outside field recognition.
        return image, self.task.vision.read_text(
            image, region=self.dialog_region(image), min_confidence=.55)

    @staticmethod
    def find(items, labels):
        return [i for i in items if label(i) in labels and i.get('confidence', 0) >= .7]

    def text_score(self, item, labels):
        normalized = label(item)
        if normalized in labels:
            return 1.0
        return max((self.matcher.evaluate(
            expected=expected, received=normalized, threshold=self.TEXT_THRESHOLD,
        ).score for expected in labels), default=0.0)

    def ranked(self, image, items, labels, field_name):
        _, dialog_y, _, dialog_height = self.dialog_region(image)
        expected_y = .34 if field_name == 'title' else .50
        ranked = []
        for item in items:
            confidence = float(item.get('confidence', 0))
            if confidence < .55:
                continue
            text_score = self.text_score(item, labels)
            if text_score < self.TEXT_THRESHOLD:
                continue
            relative_y = (item['center'][1] - dialog_y) / max(1, dialog_height)
            position_score = max(0.0, 1.0 - abs(relative_y - expected_y) / .45)
            explicit = label(item) in (
                {'title', 'title required'} if field_name == 'title'
                else {'description', 'description required'})
            score = .65 * text_score + .20 * confidence + .15 * position_score
            ranked.append((round(score, 6), item, explicit))
        # A visible field label is safer than a placeholder when Facebook
        # renders both for the same input.
        if any(candidate[2] for candidate in ranked):
            ranked = [candidate for candidate in ranked if candidate[2]]
        ranked.sort(key=lambda candidate: (-candidate[0], candidate[1]['center'][1], candidate[1]['center'][0]))
        return [(score, item) for score, item, _ in ranked]

    def candidate(self, image, items, labels, field_name):
        ranked = self.ranked(image, items, labels, field_name)
        if not ranked:
            return None, 0, False
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < self.AMBIGUITY_MARGIN:
            return None, len(ranked), False
        return ranked[0][1], len(ranked), False

    def observe_field(self, labels, field_name):
        image, items = self.read_dialog()
        candidate, count, anchored = self.candidate(image, items, labels, field_name)
        if candidate is None and field_name == 'description' and phrase(items, 'create post') and len(self.find(items, {'save'})) == 1:
            # Description is a placeholder: it disappears once text is saved.
            # Anchor the editable body below the required Title input.
            candidate, count, _ = self.candidate(image, items, self.TITLE, 'title')
            anchored = candidate is not None
        return image, items, candidate, count, anchored

    def stable_field(self, labels, field_name):
        previous = None
        stable = 0
        largest_count = 0
        for attempt in range(self.MAX_FIELD_OBSERVATIONS):
            _, _, candidate, count, anchored = self.observe_field(labels, field_name)
            largest_count = max(largest_count, count)
            observation = (candidate, anchored)
            if (candidate is not None and previous is not None and anchored == previous[1]
                    and self.task.same(candidate, previous[0])):
                stable += 1
            else:
                stable = 1 if candidate is not None else 0
            previous = observation if candidate is not None else None
            if stable >= self.STABLE_OBSERVATIONS:
                return candidate, anchored
            if attempt + 1 < self.MAX_FIELD_OBSERVATIONS:
                time.sleep(.2)
        raise RuntimeError(
            f'Could not identify a stable Live {field_name} field '
            f'(candidate_count={largest_count}). Check Live Producer.'
        )

    def field(self, labels, value):
        field_name = 'title' if labels == self.TITLE else 'description'
        target, anchored = self.stable_field(labels, field_name)
        if anchored:
            x, y = target['center']
            target = dict(target, center=(x, y + 80))
        # Title's required label is inside its input; Description's label
        # sits above the editable body. Clicking the latter selects page text.
        elif field_name == 'description' or not label(target).endswith(' required'):
            x, y = target['center']
            target = dict(target, center=(x, y + max(28, target['bounds'][3] - target['bounds'][1] + 12)))
        self.task.click(target)
        self.task.client.exec_cmd(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'])
        self.task.client.exec_cmd(['xdotool', 'type', '--clearmodifiers', '--', value])
        # Verify the focused field, including Unicode and multiline text, using
        # the browser's native Copy action. Keep the clipboard transient.
        self.task.client.exec_cmd(['xdotool', 'key', '--clearmodifiers', 'ctrl+a', 'ctrl+c'])
        time.sleep(.2)
        copied = self.task.client.exec_cmd(['timeout', '3s', 'xclip', '-o', '-selection', 'clipboard']).stdout
        if copied.replace('\r\n', '\n').strip() != value.strip():
            raise RuntimeError('Facebook did not accept the Live title or caption. Check the post details before starting.')
        self.task.client.exec_cmd(['xdotool', 'key', '--clearmodifiers', 'Right'])

    def fill(self, frame, title, caption):
        if not title.strip() or not caption.strip() or len(title) > 255 or len(caption) > 5000:
            raise RuntimeError('Enter a Live title and caption before starting the video.')
        candidates = self.find(frame.regions.get('details', []), self.OPEN)
        fresh = self.task.capture('prepare')
        matches = self.find(fresh.regions.get('details', []), self.OPEN)
        if (not fresh.signals['setup_panel'] or fresh.signals['active_surface'] or fresh.signals['ended_surface']
                or len(candidates) != 1 or len(matches) != 1 or not self.task.same(candidates[0], matches[0])):
            raise RuntimeError('Could not open the Live post details. Check Live Producer.')
        self.task.click(matches[0])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, items = self.read_dialog()
            if self.find(items, self.TITLE) and (self.find(items, self.CAPTION)
                    or (phrase(items, 'create post') and len(self.find(items, {'save'})) == 1)):
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Facebook did not open the Live post details form.')
        self.field(self.TITLE, title)
        self.field(self.CAPTION, caption)
        image, items = self.read_dialog()
        saves = self.find(items, {'save'})
        fresh_image, fresh_items = self.read_dialog()
        matches = self.find(fresh_items, {'save'})
        if (len(saves) != 1 or len(matches) != 1 or not self.task.same(saves[0], matches[0])
                or not enabled_color(image, saves[0]) or not enabled_color(fresh_image, matches[0])):
            raise RuntimeError('Facebook has not enabled Save for the Live post details.')
        self.task.click(matches[0])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            frame = self.task.capture('prepare')
            if frame.signals['details_saved'] and frame.signals['setup_panel']:
                return frame
            time.sleep(.5)
        raise RuntimeError('Facebook did not confirm the Live post details were saved.')
