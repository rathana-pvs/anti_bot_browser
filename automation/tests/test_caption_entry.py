"""Offline entry policy/controller tests: no browser, Docker, or real sleeps."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from engine.behavior_profile import BehaviorSession
from engine.behavior_timing import BehaviorTiming, InteractionWait, EntryInterrupted
from engine.text_entry_policy import choose_entry_plan, contains_link
from engine.human_input import HumanInput
from modules.composer_entry import ComposerEntry, ClipboardFailure
from modules.composer_review import ComposerReview
from tasks.base_task import BaseTask


def session():
    return BehaviorSession('medium', 321, 'attempt')


def test_short_caption_both_choices_and_independent_streams():
    behavior = session()
    with patch.object(behavior, 'stream', return_value=Mock(random=lambda: .1)):
        assert choose_entry_plan('a short caption', 'caption', behavior).method == 'type'
    with patch.object(behavior, 'stream', return_value=Mock(random=lambda: .9)):
        assert choose_entry_plan('a short caption', 'caption', behavior).method == 'paste'
    first, second = session(), session()
    first.stream('mouse').random()
    assert choose_entry_plan('a short caption', 'caption', first) == choose_entry_plan('a short caption', 'caption', second)
    assert first.typing_speed() == first.typing_speed()


@pytest.mark.parametrize('words', [100, 101, 300])
def test_long_captions_paste_without_random_draw(words):
    behavior = session()
    with patch.object(behavior, 'stream', side_effect=AssertionError('Do not draw')):
        assert choose_entry_plan('word ' * words, 'caption', behavior).method == 'paste'


def test_99_words_eligible_for_typing():
    behavior = session()
    with patch.object(behavior, 'stream', return_value=Mock(random=lambda: .1)):
        assert choose_entry_plan(' '.join(['word'] * 99), 'caption', behavior).method == 'type'


@pytest.mark.parametrize('text', ['See HTTPS://example.com/a?b=1.', 'www.example.com', 'visit example.com/path', 'https://例子.中国/a', 'example.xn--p1ai'])
def test_links_always_paste(text):
    assert contains_link(text)
    assert choose_entry_plan(text, 'caption', session()).method == 'paste'


@pytest.mark.parametrize('text', ['សួស្តី', 'hello 😊', 'one\ntwo', 'one\rtwo', 'one\ttwo'])
def test_unsupported_typing_routes_to_full_paste(text):
    assert choose_entry_plan(text, 'caption', session()).method == 'paste'


def test_explicit_comment_context_forces_paste():
    assert choose_entry_plan('short', 'comment', session()).method == 'paste'
    assert choose_entry_plan('', 'caption', session()).method == 'skip'


class Clock:
    def __init__(self): self.now = 0
    def clock(self): return self.now
    def sleep(self, value): self.now += value


def fixture_task():
    task = SimpleNamespace(behavior_session=session(), human=Mock(), client=Mock(),
                           log=Mock(), telemetry=Mock(), capture_evidence=Mock(),
                           input_cancelled=False, paste_text=Mock())
    task.client.input_owner.return_value = ('chrome', '10,20')
    task.client.get_chrome_window.return_value = 'chrome'
    controller = ComposerEntry(task)
    clock = Clock()
    controller.waiter = lambda owner, deadline=None, surface_guard=None: InteractionWait(
        guard=lambda: not task.input_cancelled and task.client.input_owner() == owner,
        clock=clock.clock, sleep=clock.sleep, surface_guard=surface_guard,
    )
    return task, controller


def test_paste_readback_once_and_plan_is_reused():
    task, entry = fixture_task()
    entry.read_field = Mock(side_effect=['', 'https://example.com', 'https://example.com'])
    assert entry.enter('https://example.com', (2, 3), empty_confirmed=True).verified
    assert entry.enter('https://example.com', (2, 3), empty_confirmed=True).status == 'reused_existing_content'
    task.paste_text.assert_called_once_with('https://example.com', expected_owner=('chrome', '10,20'))
    task.human.type_text.assert_not_called()
    assert len(entry.plans) == 1


@pytest.mark.parametrize('draft,status', [('different', 'draft_conflict'), (None, 'draft_unknown')])
def test_conflicting_or_unknown_draft_never_inserted(draft, status):
    task, entry = fixture_task()
    entry.read_field = Mock(return_value=draft)
    assert entry.enter('https://example.com', (2, 3)).status == status
    task.paste_text.assert_not_called()
    task.human.type_text.assert_not_called()


def test_matching_draft_skips_input():
    task, entry = fixture_task()
    entry.read_field = Mock(return_value='https://example.com')
    assert entry.enter('https://example.com', (2, 3)).verified
    task.paste_text.assert_not_called()


def test_clipboard_failure_never_types():
    task, entry = fixture_task()
    entry.read_field = Mock(return_value='')
    task.paste_text.side_effect = ClipboardFailure('failed')
    assert entry.enter('https://example.com', (2, 3)).status == 'clipboard_failed'
    task.human.type_text.assert_not_called()


def test_partial_insertion_does_not_repeat_or_switch():
    task, entry = fixture_task()
    entry.read_field = Mock(side_effect=['', 'https://exam', 'https://exam'])
    assert not entry.enter('https://example.com', (2, 3)).verified
    assert not entry.enter('https://example.com', (2, 3)).verified
    task.paste_text.assert_called_once()
    task.human.type_text.assert_not_called()


def test_focus_loss_interrupts_before_insertion():
    task, entry = fixture_task()
    task.client.input_owner.side_effect = [('chrome', '10,20'), ('other', '10,20')]
    assert entry.enter('https://example.com', (2, 3)).status == 'interrupted'
    task.paste_text.assert_not_called()


def test_composer_closure_prevents_insertion():
    task, entry = fixture_task()
    entry.read_field = Mock(return_value='')
    assert entry.enter('https://example.com', (2, 3), surface_guard=lambda: False).status == 'interrupted'
    task.paste_text.assert_not_called()


def test_empty_clipboard_marker_requires_current_placeholder_confirmation():
    task, entry = fixture_task()
    marker = []
    task.client.write_clipboard.side_effect = marker.append
    task.client.read_clipboard.side_effect = lambda: marker[-1]
    assert entry.read_field(entry.waiter(('chrome', '10,20')), lambda: False) is None
    assert entry.read_field(entry.waiter(('chrome', '10,20')), lambda: True) == ''
    assert all('Return' not in call.args[0] for call in task.client.xdo.call_args_list)


def test_strict_paste_helper_has_no_typing_fallback():
    task = BaseTask.__new__(BaseTask)
    task.client = Mock()
    task.client.write_clipboard.side_effect = RuntimeError('secret')
    task.human = Mock()
    task.input_cancelled = False
    with pytest.raises(ClipboardFailure, match='typing fallback is disabled'):
        task.paste_text('https://example.com')
    task.human.type_text.assert_not_called()
    task.human.key_press.assert_not_called()


def test_transport_overhead_is_not_slept_twice_and_no_return():
    clock = Clock()
    client = Mock()
    client.type_character.side_effect = lambda char: clock.sleep(.4)
    human = HumanInput(client, session())
    result = human.type_text('abc', wpm=60, waiter=InteractionWait(clock=clock.clock, sleep=clock.sleep))
    assert result['pause_seconds'] == 0
    assert result['dispatch_seconds'] == pytest.approx(1.2)
    assert result['effective_wpm'] == pytest.approx(30)
    client.xdo.assert_not_called()
    with pytest.raises(ValueError): human.type_text('one\ntwo')


def test_takeover_stops_further_characters():
    clock = Clock()
    client = Mock()
    client.type_character.side_effect = lambda char: clock.sleep(.1)
    waiter = InteractionWait(guard=lambda: client.type_character.call_count < 2,
                             clock=clock.clock, sleep=clock.sleep)
    with pytest.raises(EntryInterrupted): HumanInput(client, session()).type_text('abcd', waiter=waiter)
    assert client.type_character.call_count == 2


def test_review_budget_is_bounded_and_not_repeated():
    behavior = session()
    timing = BehaviorTiming(behavior)
    assert 2 <= timing.review('word ' * 500, 30) <= 12
    review = ComposerReview(behavior)
    clock = Clock()
    waiter = InteractionWait(clock=clock.clock, sleep=clock.sleep)
    assert review.review('caption', 'media', 1, waiter) > 0
    elapsed = clock.now
    assert review.review('caption', 'media', 1, waiter) == 0
    assert clock.now == elapsed
    assert review.review('changed caption', 'media', 1, waiter) > 0


def test_long_paste_budget_does_not_include_typing_time():
    import time
    task, entry = fixture_task()
    text = 'long caption ' * 1000
    entry.read_field = Mock(side_effect=['', text])
    assert entry.enter(text, (2, 3), deadline=time.monotonic() + 20).verified
    task.human.type_text.assert_not_called()


def test_cancellation_before_entry_does_not_refocus():
    task, entry = fixture_task()
    task.input_cancelled = True
    assert entry.enter('https://example.com', (2, 3)).status == 'interrupted'
    task.human.click.assert_not_called()
    task.paste_text.assert_not_called()


def test_non_chrome_focus_rejects_entry():
    task, entry = fixture_task()
    task.client.get_chrome_window.return_value = 'another-window'
    assert entry.enter('https://example.com', (2, 3)).status == 'interrupted'
    task.paste_text.assert_not_called()


def test_content_changed_during_review_blocks_publication():
    task = BaseTask.__new__(BaseTask)
    task.composer_entry = Mock()
    task.composer_entry.last_owner = ('chrome', '10,20')
    task.composer_entry.read_field.return_value = 'operator changed the caption'
    task.composer_review = Mock()
    task.composer_review.review.return_value = 2
    task.telemetry = Mock()
    with pytest.raises(ClipboardFailure, match='changed during review'):
        task.review_composer('requested caption')
    task.telemetry.record_step.assert_not_called()
