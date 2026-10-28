"""Whole-caption entry, conservative draft checks, and exact clipboard readback."""
from dataclasses import dataclass
import time
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
        client.xdo('key ctrl+a')
        waiter.check()
        client.xdo('key ctrl+c')
        waiter.pause(.15)
        value = client.read_clipboard()
        waiter.check()
        client.xdo('key Right')  # Collapse selection without changing the draft.
        if value == marker:
            confirmed = empty_confirmed() if callable(empty_confirmed) else empty_confirmed
            return '' if confirmed else None
        return value

    def enter(self, text, target, *, field='caption', empty_confirmed=False, deadline=None, surface_guard=None):
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
        interruption_reason = None
        status = 'entry_unconfirmed'
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
            if current == text:
                self.completed[key] = True
                status = 'reused_existing_content'
                return EntryResult(status, True, 'clipboard_exact')
            if key in self.completed:
                status = 'previous_entry_changed'
                return EntryResult(status)
            if current is None or current != '':
                status = 'draft_unknown' if current is None else 'draft_conflict'
                return EntryResult(status)
            if deadline is not None:
                estimate = (len(text) * 60 / (plan.wpm * 5) + len(text.split()) * .3 + 5) if plan.method == 'type' else 5
                if time.monotonic() + estimate >= deadline:
                    status = 'insufficient_entry_budget'
                    return EntryResult(status)
            if plan.method == 'paste':
                waiter.checkpoint()
                task.paste_text(text, expected_owner=owner)
                waiter.pause(self.timing.sample(.2, .35, .6))
            else:
                stats = task.human.type_text(text, wpm=plan.wpm, waiter=waiter)
            waiter.check()
            entered = self.read_field(waiter)
            if entered != text:
                status = 'entry_unconfirmed'
                return EntryResult(status)
            self.completed[key] = True
            status = 'entered_verified'
            return EntryResult(status, True, 'clipboard_exact')
        except EntryInterrupted as exc:
            status = 'interrupted'
            interruption_reason = exc.reason
            task.log('WARNING', f'Caption entry interrupted: {interruption_reason}.')
            return EntryResult(status, interruption_reason=interruption_reason)
        except ClipboardFailure:
            status = 'clipboard_failed'
            return EntryResult(status)
        except Exception:
            # Do not expose clipboard contents or command payloads in errors.
            status = 'entry_unconfirmed'
            return EntryResult(status)
        finally:
            if interruption_reason is not None:
                stats['interruption_reason'] = interruption_reason
            task.telemetry.record_step('caption_entry', (time.monotonic() - started) * 1000,
                                       outcome=status, **plan.summary(), **stats)
