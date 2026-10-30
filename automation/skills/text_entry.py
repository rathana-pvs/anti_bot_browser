"""Putting text into a Facebook field and reviewing it before publishing.

``ComposerEntry`` (modules/composer_entry.py) chooses typing or paste per the
entry policy and verifies the field; this skill owns the clipboard paste it
falls back on and the pre-publish review.
"""

from modules.composer_entry import ClipboardFailure, CaptionReviewFailure


class TextEntry:
    def __init__(self, task):
        self.task = task

    def paste(
        self,
        text: str,
        *,
        paste_key: str = "ctrl+v",
        selection: str = "clipboard",
        expected_owner=None,
    ) -> None:
        """
        Copy text (including full Unicode & emojis) to container X11 clipboard
        using xclip and paste it into the active element with the requested
        shortcut. ``primary`` + Shift+Insert is available as an independent
        fallback when a page ignores the regular clipboard shortcut.
        """
        task = self.task
        try:
            owner = expected_owner if expected_owner is not None else task.client.input_owner()
            if task.input_cancelled or task.client.input_owner()[0] != owner[0]:
                raise ClipboardFailure("Input ownership lost before clipboard write")
            task.client.write_clipboard(text, selection)
            if task.input_cancelled or task.client.input_owner()[0] != owner[0]:
                raise ClipboardFailure("Input ownership lost before paste")
            task.human.key_press(paste_key)
        except Exception:
            raise ClipboardFailure("Clipboard paste failed; typing fallback is disabled") from None

    def review(self, caption: str, media_revision: str = "", image_count: int = 0):
        task = self.task
        owner = getattr(task.composer_entry, "last_owner", None) or task.client.input_owner()
        duration = task.composer_review.review(
            caption, media_revision, image_count, task.composer_entry.waiter(owner),
        )
        if caption and hasattr(task.composer_entry, "last_owner"):
            waiter = task.composer_entry.waiter(owner)
            observed = task.composer_entry.read_field(waiter)
            if observed != caption:
                reason = ('caption_empty_after_review' if observed == '' else
                          'caption_unreadable_after_review' if observed is None else
                          'caption_changed_after_review')
                task.telemetry.record_step('composer_review', duration * 1000,
                                           outcome='unconfirmed', reason=reason)
                raise CaptionReviewFailure(reason)
        task.telemetry.record_step("composer_review", duration * 1000, outcome="completed")

