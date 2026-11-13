"""Whole-caption entry, conservative draft checks, and exact clipboard readback.

The text is cleaned before entry (`clean_text`) and read back exactly; only differences nobody can see
(`same_text`) are ignored. A readback that differs is read once more, as a slow container can copy the
field before the paste has landed."""
from dataclasses import dataclass
import re
import time
import unicodedata
import uuid
from engine.behavior_timing import BehaviorTiming, EntryInterrupted, InteractionWait
from engine.text_entry_policy import choose_entry_plan


class ClipboardFailure(RuntimeError):
    """Clipboard dispatch failed; insertion may be uncertain. Never type fallback."""


class CaptionReviewFailure(ClipboardFailure):
    """A reason code safe to record without caption or clipboard contents."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Caption changed during review; no publish action permitted')


# Spaces that look like a plain space; Facebook may store any of them as one.
_SPACES = str.maketrans({c: ' ' for c in '\t\u00a0\u2007\u2009\u200a\u202f\u3000'})
# Invisible marks a field may drop or keep. Joiners (U+200C/U+200D) are kept: emoji and some scripts need them.
_INVISIBLE = dict.fromkeys(map(ord, '\u200b\u2060\ufeff'))
_REFOCUS_REASONS = ('focus_changed', 'chrome_not_focused')
READBACK_RETRY_S = 1.0


def clean_text(text):
    """The text as entered: Unicode composed, Windows line breaks made plain, look-alike spaces made plain,
    runs of spaces made one, spaces trimmed from each line and blank lines from both ends, and at most one
    blank line in a row. Nothing a reader sees changes, so Facebook has nothing left to rewrite."""
    text = unicodedata.normalize('NFC', text or '').replace('\r\n', '\n').replace('\r', '\n').translate(_SPACES)
    text = '\n'.join(re.sub(' {2,}', ' ', line).strip(' ') for line in text.split('\n'))
    return re.sub('\n{3,}', '\n\n', text).strip('\n')


def same_text(observed, expected):
    """The field holds `expected`, ignoring only differences nobody can see (see `clean_text`, plus
    zero-width marks). None (unreadable) never matches."""
    if observed is None or expected is None:
        return False
    return clean_text(observed).translate(_INVISIBLE) == clean_text(expected).translate(_INVISIBLE)


def mismatch_kind(observed, expected):
    """How a readback differs, safe to record: it never carries either text."""
    if observed is None:
        return 'unreadable'
    seen, wanted = (clean_text(value).translate(_INVISIBLE) for value in (observed, expected))
    if not seen:
        return 'empty'
    if wanted.startswith(seen):
        return 'missing_end'
    if seen.startswith(wanted):
        return 'extra_end'
    return 'differs'


def _is_partial(observed, expected):
    """The field holds a beginning of `expected` and nothing else: our own interrupted entry."""
    return mismatch_kind(observed, expected) == 'missing_end'


@dataclass(frozen=True)
class EntryResult:
    status: str
    verified: bool = False
    verification: str = 'unconfirmed'
    interruption_reason: str | None = None


class ComposerEntry:
    def __init__(self, task):
        self.task = task
        self.plans = {}
        self.completed = {}
        self.replacement_attempted = set()
        self.repaired = set()
        self.timing = BehaviorTiming(task.behavior_session)

    def waiter(self, owner, deadline=None, surface_guard=None):
        task = self.task
        def guard():
            if getattr(task, 'input_cancelled', False):
                raise EntryInterrupted('Execution cancelled', 'cancelled')
            if deadline is not None and time.monotonic() >= deadline:
                raise EntryInterrupted('Entry deadline reached', 'deadline_exceeded')
            current_owner = task.client.input_owner()
            if current_owner[0] != owner[0]:
                raise EntryInterrupted('Window focus changed', 'focus_changed')
            return True
        return InteractionWait(guard=guard, surface_guard=surface_guard, progress=lambda: task.log('INFO', 'Caption entry/review in progress.'))

    def read_field(self, waiter, empty_confirmed=False):
        client = self.task.client
        marker = 'entry-probe-' + uuid.uuid4().hex
        waiter.check()
        client.write_clipboard(marker)
        waiter.check()
        self.task.human.key_press('ctrl+a')
        waiter.check()
        self.task.human.key_press('ctrl+c')
        waiter.pause(.15)
        value = client.read_clipboard()
        waiter.check()
        self.task.human.key_press('Right')  # Collapse selection without changing the draft.
        if value == marker:
            confirmed = empty_confirmed() if callable(empty_confirmed) else empty_confirmed
            return '' if confirmed else None
        return value

    def enter(self, text, target, *, field='caption', empty_confirmed=False, deadline=None, surface_guard=None,
              replace_existing=False, before_verify=None):
        """Enter `text` into the field at `target` and read it back. Focus lost to another window is taken back
        once (a click on the field, then the field is read again before anything else); a field left holding
        only a beginning of this text by an interrupted entry is cleared, confirmed empty, and entered again."""
        if replace_existing and (field != 'comment' or not callable(surface_guard)):
            raise ValueError('Preset replacement requires a guarded comment editor')
        task = self.task
        key = (field, text)
        plan = self.plans.setdefault(key, None)
        if plan is None:
            plan = choose_entry_plan(text, field, task.behavior_session)
            self.plans[key] = plan
        if plan.method == 'skip':
            return EntryResult('skipped', True, 'empty_request')
        task.capture_evidence('caption_entry_plan', **plan.summary())
        started = time.monotonic()
        stats = {}
        result = self._enter_once(text, target, key, plan, stats, empty_confirmed, deadline, surface_guard,
                                  replace_existing, before_verify)
        if result.interruption_reason in _REFOCUS_REASONS and not getattr(task, 'input_cancelled', False):
            task.log('INFO', f'Caption focus was lost ({result.interruption_reason}); clicking the field once more.')
            stats['refocused'] = True
            result = self._enter_once(text, target, key, plan, stats, empty_confirmed, deadline, surface_guard,
                                      replace_existing, before_verify)
        if result.interruption_reason is not None:
            stats['interruption_reason'] = result.interruption_reason
        task.telemetry.record_step('caption_entry', (time.monotonic() - started) * 1000,
                                   outcome=result.status, **plan.summary(), **stats)
        return result

    def _clear_partial(self, waiter, empty_confirmed):
        """Select all and delete, then read the field: True only once it is confirmed empty."""
        waiter.checkpoint()
        self.task.human.key_press('ctrl+a')
        waiter.check()
        self.task.human.key_press('Delete')
        waiter.pause(.2)
        return self.read_field(waiter, empty_confirmed) == ''

    def _read_back(self, waiter, text):
        entered = self.read_field(waiter)
        if not same_text(entered, text):
            waiter.pause(READBACK_RETRY_S)  # a slow container can copy before the paste has landed
            entered = self.read_field(waiter)
        return entered

    def _enter_once(self, text, target, key, plan, stats, empty_confirmed, deadline, surface_guard,
                    replace_existing, before_verify):
        task = self.task
        entered_text = clean_text(text)
        try:
            if getattr(task, 'input_cancelled', False):
                raise EntryInterrupted('Execution cancelled before focus', 'cancelled')
            task.human.click(*target)
            owner = task.client.input_owner()
            chrome_window = str(task.client.get_chrome_window())
            if owner[0] != chrome_window:
                task.log('WARNING', f'Caption focus mismatch: focused_window={owner[0]}, chrome_window={chrome_window}.')
                raise EntryInterrupted('Expected Chrome window is not focused', 'chrome_not_focused')
            self.last_owner = owner
            waiter = self.waiter(owner, deadline, surface_guard)
            waiter.pause(self.timing.sample(.15, .25, .4))
            current = self.read_field(waiter, empty_confirmed)
            if same_text(current, text):
                self.completed[key] = True
                return EntryResult('reused_existing_content', True, 'clipboard_exact')
            if key in self.completed or key in self.replacement_attempted:
                return EntryResult('previous_entry_changed')
            if (current and not replace_existing and _is_partial(current, text)
                    and key not in self.repaired):
                self.repaired.add(key)
                stats['repaired_partial'] = True
                task.log('INFO', 'The field holds part of this text from an interrupted entry; clearing it.')
                if not self._clear_partial(waiter, empty_confirmed):
                    return EntryResult('draft_unknown')
                current = ''
            if current is None or (current != '' and not replace_existing):
                return EntryResult('draft_unknown' if current is None else 'draft_conflict')
            if deadline is not None:
                estimate = (len(entered_text) * 60 / (plan.wpm * 5) + len(entered_text.split()) * .3 + 5) if plan.method == 'type' else 5
                if time.monotonic() + estimate >= deadline:
                    return EntryResult('insufficient_entry_budget')
            if plan.method == 'paste':
                waiter.checkpoint()
                if replace_existing and current != '':
                    # Only a verified Live preset may replace saved content.
                    # The first read is enough; retain editor/focus guards.
                    task.human.key_press('ctrl+a')
                    waiter.checkpoint()
                if replace_existing:
                    self.replacement_attempted.add(key)
                task.paste_text(entered_text, expected_owner=owner)
                waiter.pause(self.timing.sample(.2, .35, .6))
            else:
                # The field is read back right below, so a corrected slip can never stay.
                typed = task.human.type_text(entered_text, wpm=plan.wpm, waiter=waiter, allow_typos=True)
                if isinstance(typed, dict):
                    stats.update(typed)
            if before_verify is not None:
                waiter.checkpoint()
                before_verify(waiter)
                waiter.checkpoint()
            waiter.check()
            entered = self._read_back(waiter, text)
            if not same_text(entered, text):
                stats['mismatch'] = mismatch_kind(entered, text)
                stats['length_delta'] = len(clean_text(entered or '')) - len(entered_text)
                interruption_reason = 'field_content_changed' if before_verify is not None else None
                return EntryResult('entry_unconfirmed', interruption_reason=interruption_reason)
            self.completed[key] = True
            return EntryResult('entered_verified', True, 'clipboard_exact')
        except EntryInterrupted as exc:
            task.log('WARNING', f'Caption entry interrupted: {exc.reason}.')
            return EntryResult('interrupted', interruption_reason=exc.reason)
        except ClipboardFailure:
            return EntryResult('clipboard_failed')
        except Exception:
            # Do not expose clipboard contents or command payloads in errors.
            return EntryResult('entry_unconfirmed')
