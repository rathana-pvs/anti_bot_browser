"""Failure-only Live evidence with background credentials excluded."""
import cv2
import numpy as np

from engine.evidence import EvidenceRecorder


SAFE_LABELS = frozenset({
    'save', 'cancel', 'create post', 'edit post', 'edit post details',
    'title', 'title required', 'add a title', 'add a title required',
    'description', 'description required', 'add a description',
    'say something about this live video', 'say something about your live video',
    'go live', 'end live', 'end live video', 'live video has ended',
    'preset pinned comment', 'enable preset pinned comment', 'edit',
})


class LiveFailureEvidence:
    def __init__(self, profile_id):
        self.profile_id = profile_id
        self.observation = None
        self.last_dialog = None

    @staticmethod
    def dialog_bounds(image, items):
        """Locate a closed rectangular modal containing its heading and Save.

        The credential-bearing background must never be included merely because
        a fixed OCR region used to cover a dialog.
        """
        headings = [i for i in items if ' '.join(str(i.get('text', '')).lower().split())
                    in {'create post', 'edit post', 'edit post details'}]
        saves = [i for i in items if str(i.get('text', '')).strip().lower() == 'save']
        if not headings or len(saves) != 1:
            return None
        height, width = image.shape[:2]
        edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 40, 120)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (.20*width <= w <= .45*width and .12*height <= h <= .94*height
                    and abs(x+w/2-width/2) <= .08*width):
                continue
            polygon = cv2.approxPolyDP(contour, .015*cv2.arcLength(contour, True), True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            def inside(item):
                a, b, c, d = item['bounds']
                return x < a < c < x+w and y < b < d < y+h
            if inside(saves[0]) and any(inside(i) for i in headings):
                if any(inside(i) and any(secret in str(i.get('text', '')).lower()
                                        for secret in ('stream key', 'server url', 'rtmp')) for i in items):
                    continue
                # Insets exclude the modal border/shadow and background.
                candidates.append((w*h, (x+3, y+3, x+w-3, y+h-3)))
        return min(candidates)[1] if candidates else None

    def observe(self, image, items, source, checks=None):
        # Mask before retaining the frame. A verified post-details modal may
        # retain its own contents; credentials/video behind it stay masked.
        masked = np.zeros_like(image)
        controls = []
        height, width = image.shape[:2]
        for item in items:
            text = ' '.join(str(item.get('text', '')).lower().split())
            if text not in SAFE_LABELS:
                continue
            x1, y1, x2, y2 = (int(v) for v in item['bounds'])
            x1, x2 = max(0, x1), min(width, x2)
            y1, y2 = max(0, y1), min(height, y2)
            # Reject implausibly large OCR boxes rather than revealing a panel.
            if not (0 < x2-x1 <= width * .35 and 0 < y2-y1 <= height * .08):
                continue
            masked[y1:y2, x1:x2] = image[y1:y2, x1:x2]
            controls.append({'label': text, 'bounds': [x1, y1, x2, y2],
                             'confidence': float(item.get('confidence', 0))})
        bounds = self.dialog_bounds(image, items) if source == 'post_details_dialog' else None
        if bounds is not None:
            x1, y1, x2, y2 = bounds
            masked[y1:y2, x1:x2] = image[y1:y2, x1:x2]
        if source == 'pinned_comment_settings':
            # Called only after a verified setup frame. Credential panels are
            # on the left/center; retain the post/settings column on the right.
            x1, x2 = int(.60*width), int(.90*width)
            y1, y2 = int(.13*height), int(.99*height)
            masked[y1:y2, x1:x2] = image[y1:y2, x1:x2]
        self.observation = (masked, {
            'source': source, 'redaction': 'only_allowlisted_control_labels_visible',
            'controls': controls,
            'checks': checks or {},
        })
        if bounds is not None:
            self.observation[1].update(redaction='post_details_dialog_only', dialog_bounds=list(bounds))
            self.last_dialog = self.observation
        if source == 'pinned_comment_settings':
            self.observation[1]['redaction'] = 'right_post_settings_column_only'

    def save(self, action):
        if self.observation is None:
            return None
        image, metadata = self.observation
        recorder = EvidenceRecorder(self.profile_id, 'live_' + action)
        recorder.capture('failure_redacted', image, metadata)
        if self.last_dialog is not None and self.last_dialog is not self.observation:
            recorder.capture('last_confirmed_dialog', *self.last_dialog)
        recorder.write_result('failed', action=action, redacted=True)
        return recorder.directory
