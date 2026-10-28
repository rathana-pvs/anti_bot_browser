"""Guarded, reversible Live Producer preset comment setup; never publishes."""
import time
import cv2
import numpy as np
from modules.live_producer import label, phrase, enabled_color


def find_switch(image, anchor):
    """Locate the unique colored switch track beside its verified setting label."""
    h, w = image.shape[:2]
    x1 = int(anchor['bounds'][2] + 20)
    y = int(anchor['center'][1])
    y1, y2 = max(0, y-24), min(h, y+25)
    x2 = min(w, int(.90*w))
    crop = image[y1:y2, x1:x2]
    if not crop.size:
        return None
    pixels = crop.astype(float)
    b,g,r = (pixels[:,:,i] for i in range(3))
    masks = [
        (True, (b > 140) & (b > r+65) & (b > g+25)),
        (False, (np.maximum(np.maximum(b,g),r)-np.minimum(np.minimum(b,g),r) < 25)
                & (r > 65) & (r < 210)),
    ]
    tracks = []
    for enabled, mask in masks:
        contours, _ = cv2.findContours(mask.astype(np.uint8)*255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, cy, width, height = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            if 38 <= width <= 70 and 20 <= height <= 38 and 1.3 <= width/height <= 3.5 and area >= 500:
                tracks.append((x1+x, y1+cy, width, height, enabled))
    if len(tracks) != 1:
        return None
    x, cy, width, height, enabled = tracks[0]
    knob_x = x + width - height//2 if enabled else x + height//2
    return {'center': (knob_x, cy+height//2), 'enabled': enabled}


class LivePinnedComment:
    def __init__(self, task):
        self.task = task

    def read_right(self):
        image = self.task.client.screenshot()
        h,w = image.shape[:2]
        items = self.task.vision.read_text(image, region=(int(.6*w),int(.13*h),int(.30*w),int(.86*h)), min_confidence=.55)
        self.task.observe_failure_evidence(image, items, 'pinned_comment_settings', {
            'heading_count': len(self.find(items, {'preset pinned comment'})),
            'enable_label_count': len(self.find(items, {'enable preset pinned comment'})),
        })
        return image, items

    @staticmethod
    def find(items, labels):
        return [i for i in items if label(i) in labels and i.get('confidence', 0) >= .55]

    def toggle(self, image, items):
        anchors = self.find(items, {'enable preset pinned comment'})
        return find_switch(image, anchors[0]) if len(anchors) == 1 else None

    def settings(self):
        deadline = time.monotonic()+35
        scrolls = 0
        while time.monotonic() < deadline:
            image, items = self.read_right()
            switch = self.toggle(image, items)
            if switch:
                return image, items, switch
            # Long saved captions push the preset card below the fold. Use a
            # verified noninteractive panel heading to reach it, not a blind
            # click or a prerequisite that the missing card already be visible.
            headings = self.find(items, {'preset pinned comment'})
            if not headings:
                headings = self.find(items, {'drive conversions from your live video', 'add post details'})
                headings = sorted(headings, key=lambda i: i['center'][1], reverse=True)[:1]
            if scrolls < 3 and len(headings) == 1:
                _, fresh = self.read_right()
                matches = self.find(fresh, {label(headings[0])})
                if len(matches) == 1 and self.task.same(headings[0], matches[0]):
                    # Focus a noninteractive heading, then scroll the settings.
                    self.task.click(matches[0])
                    self.task.client.exec_cmd(['xdotool','key','--clearmodifiers','Page_Down'])
                    scrolls += 1
            time.sleep(.5)
        raise RuntimeError('Could not identify Facebook’s preset pinned-comment setting.')

    def set_enabled(self, desired):
        _, _, switch = self.settings()
        if switch['enabled'] == desired:
            return
        image, items = self.read_right()
        fresh = self.toggle(image, items)
        if not fresh or fresh['enabled'] != switch['enabled'] or not self.task.same(switch, fresh):
            raise RuntimeError('The pinned-comment switch moved before input.')
        self.task.click(fresh)
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            image, items = self.read_right()
            current = self.toggle(image, items)
            if current and current['enabled'] == desired:
                return
            time.sleep(.5)
        raise RuntimeError('Facebook did not confirm the pinned-comment setting.')

    @staticmethod
    def enabled_edit(image, item):
        if enabled_color(image, item):
            return True
        x1,y1,x2,y2 = item['bounds']
        crop = image[y1:y2,x1:x2]
        if not crop.size:
            return False
        b,g,r = (crop[:,:,n].astype(float) for n in range(3))
        # Edit is a blue text link on a neutral button in this layout.
        blue = (b > 140) & (b > r+65) & (b > g+25)
        return bool(blue.mean() > .10 or np.all(crop > 185,axis=2).mean() > .10)

    def open_editor(self):
        deadline = time.monotonic()+20
        scrolled = False
        while time.monotonic() < deadline:
            image, items = self.read_right()
            candidates = self.find(items, {'edit'})
            switch = self.toggle(image, items)
            if switch and switch['enabled'] and len(candidates) == 1 and self.enabled_edit(image,candidates[0]):
                fresh_image, fresh_items = self.read_right()
                matches = self.find(fresh_items, {'edit'})
                fresh_switch = self.toggle(fresh_image, fresh_items)
                if (fresh_switch and fresh_switch['enabled'] and len(matches) == 1
                        and self.task.same(candidates[0],matches[0])
                        and self.enabled_edit(fresh_image,matches[0])):
                    self.task.click(matches[0])
                    break
            if switch and switch['enabled'] and not scrolled:
                headings = self.find(items, {'preset pinned comment'})
                fresh_image, fresh_items = self.read_right()
                matches = self.find(fresh_items, {'preset pinned comment'})
                fresh_switch = self.toggle(fresh_image, fresh_items)
                if (fresh_switch and fresh_switch['enabled'] and len(headings) == len(matches) == 1
                        and self.task.same(headings[0], matches[0])):
                    # Facebook can place Edit below the fold even though the
                    # enabled switch is visible. Focus this verified card and
                    # scroll once before looking for the editor control again.
                    self.task.click(matches[0])
                    self.task.client.exec_cmd(['xdotool','key','--clearmodifiers','Page_Down'])
                    scrolled = True
            time.sleep(.5)
        else:
            raise RuntimeError('Facebook has not enabled the pinned-comment editor.')
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            image, items = self.read_editor()
            if self.is_editor(items):
                return image, items
            time.sleep(.5)
        raise RuntimeError('Facebook did not open the pinned-comment editor.')

    def read_editor(self):
        image = self.task.client.screenshot()
        h,w = image.shape[:2]
        items = self.task.vision.read_text(image,region=(int(.35*w),int(.2*h),int(.30*w),int(.7*h)),min_confidence=.55)
        return image, items

    def is_editor(self, items):
        return phrase(items,'preset pinned comment') and phrase(items,'write my own') and len(self.find(items,{'save'})) == 1

    def focus_body(self, *, click=True):
        _, items = self.read_editor()
        anchors = self.find(items, {'write my own'})
        _, fresh = self.read_editor()
        matches = self.find(fresh, {'write my own'})
        if (not self.is_editor(items) or not self.is_editor(fresh) or len(anchors) != 1 or len(matches) != 1
                or not self.task.same(anchors[0],matches[0])):
            raise RuntimeError('Could not verify the pinned-comment input.')
        item = matches[0]
        x,y = item['center']
        target = dict(item, center=(x+40,y+60))
        if click:
            self.task.click(target)
        return target

    def empty_body_visible(self):
        _, items = self.read_editor()
        placeholders = {'write a comment', 'write your comment', 'enter your comment',
                        'add a comment', 'write a pinned comment', 'write your pinned comment here'}
        return self.is_editor(items) and any(label(item) in placeholders for item in items)

    def restore_setup(self):
        _, items = self.read_right()
        headings = self.find(items,{'preset pinned comment'})
        _, fresh = self.read_right()
        matches = self.find(fresh,{'preset pinned comment'})
        if len(headings) != 1 or len(matches) != 1 or not self.task.same(headings[0],matches[0]):
            raise RuntimeError('Could not restore Live setup after configuring the comment.')
        self.task.click(matches[0])
        self.task.client.exec_cmd(['xdotool','key','--clearmodifiers','ctrl+Home'])
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            frame = self.task.capture('prepare')
            if frame.template_id in {'setup_left','setup_center','broadcast_ready'}:
                return frame
            time.sleep(.5)
        raise RuntimeError('Could not verify Live setup after configuring the comment.')

    def set(self, frame, comment):
        if not isinstance(comment,str) or len(comment) > 1000 or '\x00' in comment:
            raise RuntimeError('Pinned comment supports at most 1,000 characters.')
        if frame.template_id not in {'setup_left','setup_center','broadcast_ready'}:
            raise RuntimeError('Prepare the Live title and caption before its pinned comment.')
        desired = bool(comment.strip())
        self.set_enabled(desired)
        if desired:
            self.open_editor()
            target = self.focus_body(click=False)
            self.task.live_input.enter(comment, target, field='comment',
                                       empty_check=self.empty_body_visible,
                                       surface_guard=lambda: self.is_editor(self.read_editor()[1]))
            image,items = self.read_editor()
            fresh_image,fresh_items = self.read_editor()
            buttons,matches = self.find(items,{'save'}),self.find(fresh_items,{'save'})
            if (not self.is_editor(items) or not self.is_editor(fresh_items) or len(buttons) != 1 or len(matches) != 1
                    or not self.task.same(buttons[0],matches[0])
                    or not enabled_color(image,buttons[0]) or not enabled_color(fresh_image,matches[0])):
                raise RuntimeError('Facebook has not enabled Save for the pinned comment.')
            self.task.live_input.before_save()
            self.task.click(matches[0])
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                _, items = self.read_editor()
                if not self.is_editor(items):
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('Facebook did not confirm the pinned comment was saved.')
            # Reopen and compare the saved value rather than trusting typing alone.
            self.open_editor()
            self.focus_body()
            owner = self.task.client.input_owner()
            if self.task.live_input.composer_entry.read_field(
                    self.task.live_input.composer_entry.waiter(owner)) != comment:
                raise RuntimeError('Facebook did not retain the saved pinned comment.')
            self.task.client.exec_cmd(['xdotool','key','--clearmodifiers','Escape'])
        return self.restore_setup()
