"""Shared proof that a submitted comment finished, for Reel, Post and Live.

A comment is finished when its box is empty and no delivery notice shows on
consecutive checks. The text is never compared: Facebook shortens links, and OCR
cannot read every language. Consecutive checks catch a notice that appears just
after the box clears.
"""
import re

CHECK_SECONDS = 2.0
SETTLED_CHECKS = 3
# Text left in the box means Enter was not accepted, so stop waiting early.
BOX_NOT_EMPTY_LIMIT = 10

PENDING_NOTICES = frozenset({
    'posting', 'posting comment', 'sending', 'sending comment',
    'submitting', 'submitting comment', 'pending',
})
FAILED_NOTICES = frozenset({'retry', 'try again'})
FAILED_PREFIXES = ('couldnt post', 'could not post', 'failed to post', 'unable to post', 'comment failed')


def delivery_notice(items):
    """Return 'failed' or 'pending' when a delivery notice is visible."""
    found = None
    for item in items:
        text = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', str(item.get('text', '')).lower())).strip()
        if text in FAILED_NOTICES or text.startswith(FAILED_PREFIXES):
            return 'failed'
        # Exact matches only: a comment such as "A posting guide" is not a notice.
        if text in PENDING_NOTICES:
            found = 'pending'
    return found


class CommentDelivery:
    """Track checks after the single Enter. It never submits anything itself."""

    def __init__(self):
        self.state = 'unknown'
        self.settled_checks = 0
        self.box_not_empty_checks = 0

    @property
    def finished(self):
        return self.state == 'finished'

    def observe(self, items, box_empty):
        """Record one check and return True when there is no reason to keep waiting."""
        notice = delivery_notice(items)
        if notice:
            self.state = notice
            self.settled_checks = self.box_not_empty_checks = 0
        elif not box_empty:
            self.state = 'box_not_empty'
            self.settled_checks = 0
            self.box_not_empty_checks += 1
        else:
            self.box_not_empty_checks = 0
            self.settled_checks += 1
            self.state = 'finished' if self.settled_checks >= SETTLED_CHECKS else 'settling'
        return self.finished or self.box_not_empty_checks >= BOX_NOT_EMPTY_LIMIT
