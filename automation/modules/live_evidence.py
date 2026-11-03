"""Full-frame local evidence for Live failures, inspections and confirmed comments."""
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

    def observe(self, image, items, source, checks=None):
        # Keep the complete frame even when OCR/template recognition fails.
        # The user explicitly allows visible credentials in local screenshots.
        controls = []
        height, width = image.shape[:2]
        for item in items:
            text = ' '.join(str(item.get('text', '')).lower().split())
            if text not in SAFE_LABELS:
                continue
            x1, y1, x2, y2 = (int(v) for v in item['bounds'])
            x1, x2 = max(0, x1), min(width, x2)
            y1, y2 = max(0, y1), min(height, y2)
            if not (0 < x2-x1 <= width * .35 and 0 < y2-y1 <= height * .08):
                continue
            controls.append({'label': text, 'bounds': [x1, y1, x2, y2],
                             'confidence': float(item.get('confidence', 0))})
        self.observation = (image.copy(), {
            'source': source, 'redaction': 'none',
            'controls': controls, 'checks': checks or {},
        })

    def save(self, action, status='failed'):
        if self.observation is None:
            return None
        image, metadata = self.observation
        recorder = EvidenceRecorder(self.profile_id, 'live_' + action)
        label = 'comment_confirmed' if action == 'comment' and status == 'confirmed' else 'browser_screenshot'
        recorder.capture(label, image, metadata)
        recorder.write_result(status, action=action, redacted=False)
        return recorder.directory
