"""Read the selected broadcast's key through its verified native Copy control.

The value is transient local IPC only; never send it to logs or evidence.
"""
import re
import time
import uuid
from modules.live_producer import label


class LiveStreamKey:
    def __init__(self, task):
        self.task = task

    def copy_control(self):
        frame = self.task.capture('prepare')
        if (frame.template_id not in {'setup_left', 'setup_center', 'broadcast_ready'}
                or frame.signals['active_surface'] or frame.signals['ended_surface']):
            raise RuntimeError('Confirm the prepared Facebook broadcast before reading its stream key')
        h, w = frame.image.shape[:2]
        # Reading panels separately can split the Copy control at their shared
        # boundary. Observe the credential panel as one region on the same frame.
        x = int(.22*w)
        items = self.task.vision.read_text(frame.image,
            region=(x, int(.13*h), int(.75*w)-x, int(.70*h)), min_confidence=.55)
        anchors = [item for item in items if label(item) == 'stream key' and item.get('confidence', 0) >= .65]
        if not anchors and frame.template_id == 'setup_left':
            items = self.task.vision.read_text(frame.image,
                region=(0, int(.13*h), int(.75*w), int(.70*h)), min_confidence=.55)
            anchors = [item for item in items if label(item) == 'stream key' and item.get('confidence', 0) >= .65]
        self.task.observe_failure_evidence(frame.image, [], 'stream_key_verification', {
            'anchor_count': len(anchors),
            'anchors': [{'center': item['center'], 'confidence': item.get('confidence',0)} for item in anchors],
            'copy_controls': [{'center': item['center'], 'confidence': item.get('confidence',0)}
                              for item in items if label(item) in {'copy', 'copied'}],
            'copied_count': sum(label(item) == 'copied' for item in items),
            'label_controls': [{'label': label(item), 'center': item['center'], 'confidence': item.get('confidence',0)}
                               for item in items if label(item) in {'copy', 'copied', 'server url', 'stream key'}],
        })
        if len(anchors) != 1:
            raise RuntimeError('Could not verify the prepared broadcast stream-key field')
        anchor = anchors[0]
        buttons = [item for item in items if label(item) in {'copy', 'copied'} and item.get('confidence', 0) >= .65
                   and 0 < item['center'][1]-anchor['center'][1] < 125
                   and abs(item['center'][0]-anchor['center'][0]) < frame.image.shape[1]*.4]
        if len(buttons) != 1:
            raise RuntimeError('Could not verify a unique stream-key Copy control')
        return buttons[0]

    def ready_copy(self, previous=None):
        # Facebook temporarily changes Copy to Copied after a successful click.
        # Wait for the same observed control to become actionable again.
        for _ in range(4):
            try:
                target = self.copy_control()
            except RuntimeError as error:
                if previous is None or str(error) != 'Could not verify a unique stream-key Copy control':
                    raise
                # Some layouts show only a checkmark during Copy feedback.
                time.sleep(.6)
                continue
            if label(target) == 'copy':
                return target
            time.sleep(.6)
        raise RuntimeError('Facebook did not restore its stream-key Copy control')

    def read(self):
        values = []
        target = self.ready_copy()
        for _ in range(2):
            fresh = self.ready_copy(previous=target)
            if not self.task.same(target, fresh):
                raise RuntimeError('The stream-key Copy control changed before verification')
            self.task.client.write_clipboard('live-key-probe-' + uuid.uuid4().hex)
            self.task.click(fresh)
            time.sleep(.25)
            value = self.task.client.read_clipboard().strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', value) or value.startswith('live-key-probe-'):
                raise RuntimeError('Facebook did not provide a verified stream key')
            values.append(value)
        if values[0] != values[1]:
            raise RuntimeError('The prepared broadcast stream key changed during verification')
        return values[0]
