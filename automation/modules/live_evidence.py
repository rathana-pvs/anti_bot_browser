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


def screenshot_label(action, status, state=None):
    """`start`+`confirmed` → `live_confirmed`; a failed `comment` → `comment_not_confirmed`."""
    if status != 'failed':
        if action == 'inspect':
            return {'live': 'check_still_live', 'ended': 'check_live_ended'}.get(state, 'check_live_unconfirmed')
        return {'prepare': 'ready_to_go_live', 'start': 'live_confirmed', 'end': 'live_ended',
                'comment': 'comment_confirmed'}.get(action, action)
    return {'comment': 'comment_not_confirmed', 'start': 'go_live_failed', 'end': 'end_live_failed',
            'prepare': 'live_setup_failed', 'inspect': 'check_failed'}.get(action, action + '_failed')


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

    def save(self, action, status='failed', state=None, directory=None):
        """Saves the last frame the step looked at. `directory` is the Live job's own folder, so all
        of its steps' screenshots show together, in order."""
        if self.observation is None:
            return None
        image, metadata = self.observation
        recorder = EvidenceRecorder(self.profile_id, 'live_' + action, directory=directory)
        recorder.capture(screenshot_label(action, status, state), image, metadata)
        recorder.write_result(status, action=action, redacted=False)
        return recorder.directory
