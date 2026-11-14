"""Live comments never block preparation or retry uncertain submissions."""
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
import numpy as np
from types import SimpleNamespace
from backend.tests.test_live_human_input import adapter, Clock
from engine.behavior_timing import EntryInterrupted, InteractionWait

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from modules.live_comment import LiveComment


def entry(text, x=1200, y=800):
    return {'text': text, 'center': (x, y), 'confidence': .99, 'bounds': [x-30, y-10, x+30, y+10]}


def comment_task():
    task = Mock()
    task.same.side_effect = lambda a,b: a['center'] == b['center']
    return task


def test_comment_requires_confirmed_live_before_entry():
    task = comment_task()
    task.layouts.permits.return_value = False
    with pytest.raises(RuntimeError, match='live_not_confirmed'):
        LiveComment(task).post('hello')
    task.live_input.enter.assert_not_called()


def test_comment_after_end_can_use_ended_page_without_live_notice():
    task = comment_task()
    task.capture.return_value.image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    task.layouts.permits.return_value = False
    task.vision.read_text.return_value = [entry('Write a comment...')]
    module = LiveComment(task, after_end=True)
    assert module.observe() == task.vision.read_text.return_value
    task.layouts.permits.assert_not_called()


@pytest.mark.parametrize('items', [[], [entry('Write a comment...'), entry('Write a comment...', y=700)]])
def test_missing_or_ambiguous_input_never_submits(items):
    task = comment_task()
    module = LiveComment(task)
    module.observe = Mock(return_value=items)
    with pytest.raises(RuntimeError, match='input_missing'):
        module.post('hello')
    task.live_input.human.key_press.assert_not_called()


def test_comment_submits_once_and_requires_visible_result():
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    module.observe = Mock(side_effect=[placeholder, placeholder, [entry('hello')],
                                      placeholder + [entry('hello', y=650)]])
    module.post('hello')
    task.live_input.enter.assert_called_once()
    task.live_input.human.key_press.assert_called_once_with('Return', clear_modifiers=True)


def test_existing_visible_comment_succeeds_without_reposting():
    task = comment_task()
    module = LiveComment(task)
    module.observe = Mock(return_value=[entry('Write a comment...'), entry('hello', y=650)])
    module.post('hello')
    task.live_input.enter.assert_not_called()
    task.live_input.human.key_press.assert_not_called()


@pytest.mark.parametrize('notice', ['Posting...', 'Posting…', 'Sending…', "Couldn't post. Try again"])
@pytest.mark.parametrize('already_visible', [False, True])
def test_optimistic_comment_waits_for_delivery(monkeypatch, notice, already_visible):
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('modules.live_comment.time.sleep', clock.sleep)
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    delivered = placeholder + [entry('https://trends7news.info/ST/2033', y=600), entry('5', y=625)]
    pending = delivered + [entry(notice, y=650)]
    frames = ([pending] if already_visible else [placeholder, placeholder, pending]) + [pending, delivered]
    module.observe = Mock(side_effect=frames)
    module.post('https://trends7news.info/ST/20335')
    assert module.observe.call_count == len(frames)
    assert clock.now == 1
    assert task.live_input.human.key_press.call_count == (0 if already_visible else 1)
    assert task.live_input.enter.call_count == (0 if already_visible else 1)


def test_comment_still_posting_at_the_deadline_is_accepted_without_resubmitting(monkeypatch):
    # An ended video's page can keep "Posting…" on a comment Facebook has already saved.
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('modules.live_comment.time.sleep', clock.sleep)
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    pending = placeholder + [entry('hello', y=600), entry('Posting…', y=650)]
    module.observe = Mock(return_value=pending)
    module.post('hello')
    assert clock.now >= 90  # waited the whole budget for the notice to clear
    task.report.assert_any_call('live_comment_accepted_while_posting')
    task.live_input.enter.assert_not_called()
    task.live_input.human.key_press.assert_not_called()


@pytest.mark.parametrize('notice', ["Couldn't post. Try again", 'Try again', 'Failed to post'])
def test_comment_with_a_failure_notice_at_the_deadline_still_needs_review(monkeypatch, notice):
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('modules.live_comment.time.sleep', clock.sleep)
    task = comment_task()
    module = LiveComment(task)
    failed = [entry('Write a comment...'), entry('hello', y=600), entry(notice, y=650)]
    module.observe = Mock(return_value=failed)
    with pytest.raises(RuntimeError, match='submission_unconfirmed'):
        module.post('hello')
    task.live_input.human.key_press.assert_not_called()


def test_posting_notice_with_text_still_in_the_box_is_not_accepted(monkeypatch):
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('modules.live_comment.time.sleep', clock.sleep)
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    stuck = [entry('hello'), entry('Posting…', y=650)]  # no placeholder: the text is still in the box
    module.observe = Mock(side_effect=[placeholder, placeholder] + [stuck] * 400)  # read 3 is after Enter
    with pytest.raises(RuntimeError, match='submission_unconfirmed'):
        module.post('hello')
    task.report.assert_any_call('live_comment_submit_sent')
    assert ('live_comment_accepted_while_posting',) not in [c.args for c in task.report.call_args_list]


def test_visible_posted_comment_succeeds_without_placeholder_reconfirmation():
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    module.observe = Mock(side_effect=[placeholder, placeholder, [entry('hello', y=650)]])
    module.post('hello')
    task.live_input.human.key_press.assert_called_once_with('Return', clear_modifiers=True)


@pytest.mark.parametrize('pieces', [
    ['https://trends7news.info/ST/20', '335'],
    ['https://trends7news.info/ST/', '20335'],
    ['https://trends7news.info/', 'ST/', '20335'],
])
def test_wrapped_url_comment_is_confirmed_immediately(pieces):
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    posted = [entry(text, y=600+i*25) for i, text in enumerate(pieces)]
    module.observe = Mock(side_effect=[placeholder, placeholder, posted])
    module.post('https://trends7news.info/ST/20335')
    task.live_input.human.key_press.assert_called_once_with('Return', clear_modifiers=True)
    assert module.observe.call_count == 3


@pytest.mark.parametrize('pieces', [
    ['https://trends7news.info/ST/20', '336'],
    ['https://trends7news.info/ST/203350'],
    ['https://other.invalid/ST/20335'],
])
def test_wrapped_url_matching_rejects_different_or_partial_links(pieces):
    assert not LiveComment.contains([entry(text) for text in pieces], 'https://trends7news.info/ST/20335')


def test_normal_comment_text_keeps_word_boundaries():
    assert LiveComment.contains([entry('hello'), entry('world')], 'hello world')
    assert not LiveComment.contains([entry('hello'), entry('world')], 'helloworld')


def test_unconfirmed_submission_never_retries(monkeypatch):
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    # The text is still in the box after Enter, and the budget is already over.
    module.observe = Mock(side_effect=[placeholder, placeholder, [entry('hello')]])
    monkeypatch.setattr('modules.live_comment.time.monotonic', Mock(side_effect=[0, 91]))
    with pytest.raises(RuntimeError, match='unconfirmed'):
        module.post('hello')
    task.live_input.human.key_press.assert_called_once()


@pytest.mark.parametrize('accepted', [True, False])
def test_slow_observations_use_real_native_entry_and_never_confirm_draft(adapter, monkeypatch, accepted):
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('engine.human_input.time.sleep', clock.sleep)
    client = adapter.client
    submissions = []
    native_key = client.key
    def key(name, clear_modifiers=False, check=True):
        if name == 'Return':
            submissions.append(client.value)
            if accepted: client.value = ''
        else: native_key(name, clear_modifiers=clear_modifiers, check=check)
    client.key = key
    def waiter(owner, deadline=None, surface_guard=None):
        def guard():
            if deadline is not None and clock.now >= deadline:
                raise EntryInterrupted('Deadline', 'deadline_exceeded')
            return client.input_owner()[0] == owner[0]
        return InteractionWait(guard=guard, clock=clock.clock, sleep=clock.sleep, surface_guard=surface_guard)
    adapter.composer_entry.waiter = waiter
    task = SimpleNamespace(live_input=adapter, report=Mock(),
                           same=lambda a,b:a['center']==b['center'])
    module = LiveComment(task)
    def observe():
        clock.sleep(4)  # Production OCR plus dashboard confirmation costs seconds.
        if client.value: return [entry(client.value)]
        return [entry('Write a comment...')] + ([entry(submissions[0], y=650)] if submissions else [])
    module.observe = observe
    if accepted:
        module.post('https://example.invalid/news')
    else:
        with pytest.raises(RuntimeError, match='submission_unconfirmed'):
            module.post('https://example.invalid/news')
    assert clock.now > 20  # The old preparation reserve expired before submit.
    assert submissions == ['https://example.invalid/news']
    assert client.commands.count('key ctrl+v') == 1
    assert client.commands.count('key ctrl+c') == 2


def test_shortened_link_is_confirmed_by_emptied_input():
    task = comment_task()
    module = LiveComment(task)
    placeholder = [entry('Write a comment...')]
    draft = [entry('https://asiandot.com/article/685872afea2cea3a28d7cd05/zelenskyy-hopes-us-midterm-shifts-bring-aid')]
    shortened = placeholder + [entry('https://asiandot.com/.../zelens', y=600), entry('kyy-hopes-us-midterm...', y=625)]
    module.observe = Mock(side_effect=[placeholder, placeholder, draft, shortened])
    module.post(draft[0]['text'])
    task.live_input.human.key_press.assert_called_once_with('Return', clear_modifiers=True)
    assert module.observe.call_count == 4


def test_text_in_input_cannot_be_used_as_posted_comment_evidence(monkeypatch):
    clock = Clock()
    monkeypatch.setattr('modules.live_comment.time.monotonic', clock.clock)
    monkeypatch.setattr('modules.live_comment.time.sleep', clock.sleep)
    task = comment_task()
    module = LiveComment(task)
    def observe():
        clock.sleep(4)
        # Draft text still in the box means Enter did not send the comment.
        return [entry('hello')]
    module.observe = Mock(side_effect=[[entry('Write a comment...')], [entry('Write a comment...')]])
    task.live_input.human.key_press.side_effect = lambda *a,**k:setattr(module,'observe',observe)
    # Final pre-submit observation must also be present.
    module.observe.side_effect = [[entry('Write a comment...')]]*3
    with pytest.raises(RuntimeError, match='submission_unconfirmed'):
        module.post('hello')
    task.live_input.human.key_press.assert_called_once()
