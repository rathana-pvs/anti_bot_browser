"""Live input uses shared policy with no screenshot evidence or public actions."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from engine.behavior_timing import InteractionWait
from engine.behavior_timing import EntryInterrupted
from modules.composer_entry import EntryResult
from modules.live_input import LiveInput


class Clock:
    def __init__(self): self.now = 0
    def clock(self): return self.now
    def sleep(self, value): self.now += value


class EditableClient:
    def __init__(self, value=''):
        self.value = value
        self.clipboard = ''
        self.owner = ('chrome', '10,20')
        self.selected = False
        self.typed = []
        self.commands = []
        self.fail_clipboard = False
        self.screenshot = Mock(side_effect=AssertionError('No Live screenshot evidence'))
    def input_owner(self): return self.owner
    def get_chrome_window(self): return 'chrome'
    def write_clipboard(self, text):
        if self.fail_clipboard: raise RuntimeError('Clipboard unavailable')
        self.clipboard = text
    def read_clipboard(self): return self.clipboard
    def xdo(self, command):
        self.commands.append(command)
        if command == 'key ctrl+a': self.selected = True
        elif command == 'key ctrl+c':
            if self.value: self.clipboard = self.value
        elif command == 'key Right': self.selected = False
        elif command == 'key ctrl+v':
            self.value = self.clipboard if self.selected else self.value + self.clipboard
            self.selected = False
    def key(self, name, clear_modifiers=False, check=True): self.xdo('key ' + name)
    def type_character(self, char):
        self.typed.append(char)
        self.value = char if self.selected else self.value + char
        self.selected = False


@pytest.fixture
def adapter(monkeypatch):
    client = EditableClient()
    task = SimpleNamespace(client=client, report=Mock(), input_cancelled=False,
                           preparation_deadline=time.monotonic() + 120)
    value = LiveInput(task, 'offline-live-profile')
    clock = Clock()
    def waiter(owner, deadline=None, surface_guard=None):
        return InteractionWait(guard=lambda: not task.input_cancelled and client.input_owner()[0] == owner[0],
                               clock=clock.clock, sleep=clock.sleep, surface_guard=surface_guard)
    value.composer_entry.waiter = waiter
    # Keep real entry policy/controller/readback; isolate mouse timing only.
    value.human.click = Mock()
    monkeypatch.setattr('engine.human_input.time.sleep', clock.sleep)
    return value


def enter(adapter, text, field='live_caption', empty=True, surface=True):
    return adapter.enter(text, {'center': (20, 30)}, field=field,
                         empty_check=lambda: empty, surface_guard=lambda: surface)


@pytest.mark.parametrize('draw,method', [(.1, 'type'), (.9, 'paste')])
def test_short_live_title_and_caption_use_one_complete_method(adapter, draw, method):
    text = 'A private supplied caption'
    with patch.object(adapter.behavior_session.stream('entry_strategy'), 'random', return_value=draw):
        assert enter(adapter, text).verified
    assert adapter.client.value == text
    assert bool(adapter.client.typed) == (method == 'type')
    assert ('key ctrl+v' in adapter.client.commands) == (method == 'paste')
    adapter.client.screenshot.assert_not_called()
    assert text not in repr(adapter.events)
    assert 'Return' not in repr(adapter.client.commands)


@pytest.mark.parametrize('text,field', [
    ('word ' * 100, 'live_caption'), ('https://example.com/live?a=1', 'live_title'),
    ('Caption\nខ្មែរ 😀', 'live_caption'), ('Pinned comment', 'comment'),
    ('https://example.com', 'comment'),
])
def test_live_forced_paste_fields_preserve_exact_content(adapter, text, field):
    assert enter(adapter, text, field).verified
    assert adapter.client.value == text
    assert adapter.client.typed == []
    assert adapter.client.commands.count('key ctrl+v') == 1
    adapter.client.screenshot.assert_not_called()


def test_matching_live_draft_is_reused(adapter):
    adapter.client.value = 'Existing title'
    assert enter(adapter, 'Existing title', 'live_title').status == 'reused_existing_content'
    assert adapter.client.typed == []
    assert 'key ctrl+v' not in adapter.client.commands


def test_conflicting_live_draft_stops_without_overwrite(adapter):
    adapter.client.value = 'Operator draft'
    with pytest.raises(RuntimeError, match='draft_conflict'): enter(adapter, 'Requested title', 'live_title')
    assert adapter.client.value == 'Operator draft'
    assert adapter.client.typed == []


def test_unknown_empty_preset_body_requires_review(adapter):
    with pytest.raises(RuntimeError, match='draft_unknown'): enter(adapter, 'Pinned text', 'comment', empty=False)
    assert adapter.client.value == ''


def test_live_clipboard_failure_never_types_or_saves(adapter):
    original = adapter.client.write_clipboard
    def clipboard(text):
        if not text.startswith('entry-probe-'): raise RuntimeError('failed')
        original(text)
    adapter.client.write_clipboard = clipboard
    with pytest.raises(RuntimeError, match='clipboard_failed'): enter(adapter, 'https://example.com')
    assert adapter.client.typed == []
    assert 'key ctrl+v' not in adapter.client.commands


def test_closed_live_form_blocks_input(adapter):
    with pytest.raises(RuntimeError, match='composer_unconfirmed'): enter(adapter, 'https://example.com', surface=False)
    assert adapter.client.value == ''


def test_live_entry_respects_existing_prepare_budget(adapter):
    adapter.task.preparation_deadline = time.monotonic() + 22
    with pytest.raises(RuntimeError, match='insufficient_entry_budget'): enter(adapter, 'https://example.com')
    assert adapter.client.value == ''


def test_live_focus_loss_blocks_save(adapter):
    enter(adapter, 'https://example.com')
    adapter.client.owner = ('other-window', '10,20')
    with pytest.raises(RuntimeError, match='before Save'): adapter.before_save()


def test_live_cancellation_never_refocuses(adapter):
    adapter.task.input_cancelled = True
    with pytest.raises(RuntimeError, match='cancelled'): enter(adapter, 'Caption')
    adapter.human.click.assert_not_called()


def test_live_telemetry_allowlist_drops_content_and_secrets(adapter):
    adapter.capture_evidence('plan')
    adapter.record_step('caption_entry', 1, method='paste', text='PRIVATE', stream_key='SECRET', url='PRIVATE_URL')
    assert adapter.events == [{'name': 'caption_entry', 'duration_ms': 1, 'outcome': 'completed', 'method': 'paste'}]
    adapter.client.screenshot.assert_not_called()


@pytest.mark.parametrize('reason', ['focus_changed', 'deadline_exceeded', 'composer_unconfirmed'])
def test_live_review_error_identifies_field_and_safe_reason(adapter, reason):
    adapter.composer_entry.enter = Mock(return_value=EntryResult('entered_verified', True))
    adapter.composer_entry.last_owner = adapter.client.owner
    adapter.review.review = Mock(side_effect=EntryInterrupted('PRIVATE field contents', reason))
    with pytest.raises(RuntimeError, match='caption review was interrupted \\(' + reason + '\\)') as error:
        enter(adapter, 'PRIVATE caption')
    assert 'PRIVATE' not in str(error.value)


def test_live_review_reports_changed_content_without_exposing_it(adapter):
    adapter.composer_entry.enter = Mock(return_value=EntryResult('entered_verified', True))
    adapter.composer_entry.last_owner = adapter.client.owner
    adapter.composer_entry.read_field = Mock(return_value='PRIVATE changed draft')
    with pytest.raises(RuntimeError, match='field_content_changed') as error:
        enter(adapter, 'Requested caption')
    assert 'PRIVATE' not in str(error.value)


def test_live_pointer_movement_during_paste_and_review_does_not_interrupt(adapter):
    original = adapter.client.write_clipboard
    def write(text):
        original(text)
        adapter.client.owner = ('chrome', '99,99')
    adapter.client.write_clipboard = write
    assert enter(adapter, 'https://example.com').verified
    adapter.before_save()
    assert adapter.client.value == 'https://example.com'


def test_live_focus_loss_during_clipboard_write_prevents_paste(adapter):
    original = adapter.client.write_clipboard
    def write(text):
        original(text)
        adapter.client.owner = ('other-window', '99,99')
    adapter.client.write_clipboard = write
    with pytest.raises(RuntimeError):
        adapter.paste_text('caption', expected_owner=('chrome', '10,20'))
    assert 'key ctrl+v' not in adapter.client.commands
