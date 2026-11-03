"""Live adapter for shared input behavior, without screenshots or content logs.

Live Producer can display stream credentials. Never inherit BaseTask or attach its
EvidenceRecorder/TelemetryRecorder to this adapter.
"""
import os
import time
import uuid

from engine.behavior_profile import load_behavior_session
from engine.behavior_timing import EntryInterrupted
from engine.human_input import HumanInput
from modules.composer_entry import ClipboardFailure, ComposerEntry
from modules.composer_review import ComposerReview


class LiveInput:
    def __init__(self, task, profile_id):
        self.task = task
        self.client = task.client
        identity = os.environ.get('AUTOMATION_BEHAVIOR_RUN_ID') or uuid.uuid4().hex
        self.behavior_session = load_behavior_session(profile_id, 'live:' + identity)
        self.human = HumanInput(self.client, self.behavior_session)
        self.composer_entry = ComposerEntry(self)
        self.review = ComposerReview(self.behavior_session)
        self.telemetry = self
        self.events = []

    @property
    def input_cancelled(self):
        return getattr(self.task, 'input_cancelled', False)

    def log(self, level, message):
        # ComposerEntry's text is discarded; stdout accepts safe event names only.
        self.task.report('live_input_progress')

    def capture_evidence(self, *args, **kwargs):
        # Intentional no-op: even plan evidence must never capture Live Producer.
        return None

    def record_step(self, name, duration_ms, outcome='completed', **metadata):
        allowed = {'method', 'reason', 'word_count', 'contains_link', 'version', 'wpm',
                   'elapsed_seconds', 'dispatch_seconds', 'pause_seconds', 'effective_wpm'}
        if len(self.events) < 64:
            self.events.append({'name': name, 'duration_ms': duration_ms, 'outcome': outcome,
                                **{key: metadata[key] for key in allowed if key in metadata}})

    def paste_text(self, text, *, expected_owner):
        try:
            self.check_owner(expected_owner)
            self.client.write_clipboard(text)
            self.check_owner(expected_owner)
            self.human.key_press('ctrl+v')
        except Exception:
            raise ClipboardFailure('Live clipboard paste failed; no typing fallback') from None

    def check_owner(self, owner):
        if self.input_cancelled or self.client.input_owner()[0] != owner[0]:
            raise EntryInterrupted('Live input ownership lost')

    def deadline(self):
        # Reserve time for Save/retention checks within the existing prepare budget.
        deadline = getattr(self.task, 'preparation_deadline', None)
        return (deadline if deadline is not None else time.monotonic() + 120) - 20

    def enter(self, text, target, *, field, empty_check, surface_guard, replace_existing=False):
        field_label = {'live_title': 'title', 'live_caption': 'caption', 'comment': 'pinned comment'}.get(field, 'text')
        deadline = self.deadline()
        review_started = False
        def review(waiter):
            nonlocal review_started
            review_started = True
            self.review.review(text, field, 0, waiter)
        result = self.composer_entry.enter(
            text, target['center'], field=field, empty_confirmed=empty_check,
            surface_guard=surface_guard, deadline=deadline,
            replace_existing=replace_existing,
            before_verify=review,
        )
        if not result.verified:
            if review_started:
                if result.interruption_reason:
                    raise RuntimeError('Live ' + field_label + ' review was interrupted ('
                                       + result.interruption_reason + '); nothing was saved or published.')
                raise RuntimeError('Live ' + field_label + ' review failed (verification_unavailable); '
                                   'nothing was saved or published.')
            reason = result.interruption_reason or result.status
            raise RuntimeError('Live ' + field_label + ' entry could not be verified (' + reason
                               + '). Nothing was saved or published.')
        return result

    def before_save(self):
        try:
            self.check_owner(self.composer_entry.last_owner)
        except Exception:
            raise RuntimeError('Live input ownership was lost before Save.') from None
        # Entry stops early to reserve time for Save and retention checks.
        # Save itself may use that reserve, up to the preparation deadline.
        deadline = getattr(self.task, 'preparation_deadline', None)
        if deadline is not None and time.monotonic() >= deadline:
            raise RuntimeError('Live preparation deadline was reached before Save.')
