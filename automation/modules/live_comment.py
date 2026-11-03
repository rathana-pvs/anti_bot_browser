"""One guarded comment submission on the current Live Producer video."""
import time
from modules.live_producer import label
from live_comment_status import COMMENT_TOTAL_SECONDS, COMMENT_VERIFY_SECONDS, COMMENT_CODES, LiveCommentFailure


class LiveComment:
    PLACEHOLDERS = {'write a comment', 'write a comment...', 'write a comment…',
                    'write a public comment', 'write a public comment...',
                    'write a comment as'}

    def __init__(self, task, after_end=False):
        self.task = task
        self.after_end = after_end

    def observe(self):
        frame = self.task.capture('inspect')
        if not self.after_end and not self.task.layouts.permits(frame, 'verify_publication', state='live'):
            raise LiveCommentFailure('live_not_confirmed')
        image = frame.image
        h, w = image.shape[:2]
        # Small gray delivery notices ("Posting…") must not be filtered out
        # while the larger comment text is already visible optimistically.
        items = self.task.vision.read_text(image, region=(int(.22*w), int(.13*h), int(.78*w), int(.86*h)), min_confidence=.55)
        # Full browser evidence is retained independently of recognition.
        self.task.observe_failure_evidence(image, items, 'live_comment')
        return items

    @classmethod
    def inputs(cls, items):
        return [item for item in items if label(item) in cls.PLACEHOLDERS
                or label(item).startswith('write a comment as ')]

    @staticmethod
    def contains(items, text):
        expected = ' '.join(text.split()).casefold()
        lines = [' '.join(str(item.get('text', '')).split()).casefold() for item in items]
        # Live's narrow comments column wraps links mid-path (e.g. /20 + 335).
        # OCR line breaks are not spaces in a URL. Keep ordinary text matching
        # unchanged, and still require the complete URL, never a substring.
        url_only = expected.startswith(('https://', 'http://')) and ' ' not in expected
        return any((''.join(lines[start:end]).replace(' ', '') == expected if url_only
                    else ' '.join(lines[start:end]) == expected)
                   for start in range(len(lines)) for end in range(start+1, min(len(lines), start+8)+1))

    def post(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or '\x00' in text:
            raise RuntimeError('Live comment must contain 1–1,000 characters')
        # A normal comment has no Save/retention phase. Give native entry its
        # own deadline rather than inheriting preparation's 20-second reserve.
        deadline = time.monotonic() + COMMENT_TOTAL_SECONDS
        items = self.observe()
        targets = self.inputs(items)
        if len(targets) != 1:
            raise LiveCommentFailure('input_missing')
        target = targets[0]
        if self.contains(self.posted_items(items, target), text):
            # A matching optimistic bubble may still be uploading. Wait on it
            # rather than submitting the same comment again.
            self.wait_for_delivery(items, target, text, deadline)
            return
        fresh = self.inputs(self.observe())
        if len(fresh) != 1 or not self.task.same(target, fresh[0]):
            raise LiveCommentFailure('input_changed')

        def empty():
            return any(self.task.same(target, item) and label(item).startswith('write a ')
                       and ' as ' not in label(item) for item in self.inputs(self.observe()))

        try:
            self.task.live_input.enter(text, target, field='comment', empty_check=empty,
                surface_guard=lambda: bool(self.observe()), deadline=deadline-COMMENT_VERIFY_SECONDS)
        except RuntimeError as error:
            code = next((code for code in COMMENT_CODES if code.startswith('entry_')
                         and code.removeprefix('entry_') in str(error)), 'entry_unverified')
            raise LiveCommentFailure(code) from None
        try:
            self.task.live_input.before_save(deadline=deadline-COMMENT_VERIFY_SECONDS)
        except RuntimeError:
            raise LiveCommentFailure('submit_guard_failed') from None
        self.task.report('live_comment_submit_requested')
        self.task.live_input.human.key_press('Return', clear_modifiers=True)
        self.task.report('live_comment_submit_sent')
        self.wait_for_delivery(self.observe(), target, text, deadline)

    def wait_for_delivery(self, items, target, text, deadline):
        while time.monotonic() < deadline:
            posted = self.posted_items(items, target)
            if self.contains(posted, text) and not self.delivery_pending(posted):
                return
            time.sleep(.5)
            items = self.observe()
        raise LiveCommentFailure('submission_unconfirmed')

    @staticmethod
    def delivery_pending(items):
        for item in items:
            status = label(item)
            if (status in {'posting', 'sending', 'pending', 'retry', 'try again'}
                    or status.startswith(('posting ', 'sending ', 'couldnt post', 'could not post',
                                          'failed to post', 'unable to post', 'comment failed'))):
                return True
        return False

    @staticmethod
    def posted_items(items, target):
        return [item for item in items if item['bounds'][3] < target['bounds'][1]-16
                and item['bounds'][0] >= target['bounds'][0]-96]
