"""Bounded local Live-comment timing and content-free failure codes."""
COMMENT_TOTAL_SECONDS = 70
COMMENT_ACTION_SECONDS = 75
COMMENT_STARTUP_RESERVE_SECONDS = 15
COMMENT_VERIFY_SECONDS = 18
COMMENT_CODES = frozenset({
    'live_not_confirmed', 'input_missing', 'input_changed', 'matching_comment_visible',
    'entry_deadline_exceeded', 'entry_insufficient_entry_budget', 'entry_field_content_changed',
    'entry_focus_changed', 'entry_clipboard_failed', 'entry_draft_conflict', 'entry_draft_unknown',
    'entry_composer_unconfirmed', 'entry_unverified', 'submit_guard_failed',
    'submission_unconfirmed', 'action_timeout', 'startup_budget_insufficient', 'comment_failed',
})


class LiveCommentFailure(RuntimeError):
    def __init__(self, failure_code):
        self.failure_code = failure_code if failure_code in COMMENT_CODES else 'comment_failed'
        super().__init__('Live comment needs review (' + self.failure_code + '); do not submit again automatically')
