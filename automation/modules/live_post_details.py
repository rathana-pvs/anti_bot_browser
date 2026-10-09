"""Fill Live's reversible post details using fresh OCR and X11 input.

No screenshots, field contents or clipboard values are written to evidence.
This module never clicks Go live or starts ingest.
"""
import time

from modules.live_producer import label, phrase, enabled_color


class LivePostDetails:
    OPEN = {'whats your live video about', 'create post', 'edit post details'}
    TITLE = {'title', 'title required', 'add a title', 'add a title required'}
    CAPTION = {'description', 'description required', 'say something about this live video',
               'say something about your live video', 'add a description'}

    def __init__(self, task):
        self.task = task

    def read_dialog(self):
        image = self.task.client.screenshot()
        h, w = image.shape[:2]
        # The post form is narrower than the End Live confirmation region.
        # Keep background setup labels outside field recognition.
        x, y, rw, rh = self.task.layouts.config['regions'].get('post_details_dialog', (.35, .25, .30, .60))
        return image, self.task.vision.read_text(image, region=(int(x*w), int(y*h), int(rw*w), int(rh*h)), min_confidence=.7)

    @staticmethod
    def find(items, labels):
        return [i for i in items if label(i) in labels and i.get('confidence', 0) >= .7]

    def field(self, labels, value):
        _, items = self.read_dialog()
        candidates = self.find(items, labels)
        # Prefer the visible field label over its placeholder if both are present.
        explicit = [i for i in candidates if label(i) in {'title', 'title required', 'description', 'description required'}]
        candidates = explicit or candidates
        filled_caption = not candidates and labels == self.CAPTION
        if filled_caption and phrase(items, 'create post') and len(self.find(items, {'save'})) == 1:
            # Description is a placeholder: it disappears once text is saved.
            # The editable body remains directly below the required title input.
            candidates = self.find(items, {'title required'})
        if len(candidates) != 1:
            raise RuntimeError('Could not identify the Live post details fields. Check Live Producer.')
        _, fresh = self.read_dialog()
        matches = self.find(fresh, {label(candidates[0])})
        if len(matches) != 1 or not self.task.same(candidates[0], matches[0]):
            raise RuntimeError('Live post details moved before input. Check Live Producer.')
        target = matches[0]
        if filled_caption:
            if not phrase(fresh, 'create post') or len(self.find(fresh, {'save'})) != 1:
                raise RuntimeError('Live post details moved before input. Check Live Producer.')
            x, y = target['center']
            target = dict(target, center=(x, y + 80))
        # Title's required label is inside its input; Description's label
        # sits above the editable body. Clicking the latter selects page text.
        if not filled_caption and explicit and (label(target).startswith('description') or not label(target).endswith(' required')):
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
