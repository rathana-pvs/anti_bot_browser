"""Live's profile fallback checks only the newest timestamp and submits once."""
from unittest.mock import Mock

import numpy as np
import pytest

from backend.tests.test_live_human_input import Clock
from modules.live_comment import LiveComment
from modules.live_profile_comment import LiveProfileComment
from live_comment_status import LiveCommentFailure


def item(text, x, y):
    return {'text':text, 'center':(x,y), 'bounds':[x-35,y-10,x+35,y+10], 'confidence':.99}


def profile_items(age='1m', field=True):
    rows = [item('Posts',1080,300), item('Filters',1600,300), item('List view',1200,360),
            item('Example Page',1120,415), item(age,1120,440),
            item('A completely different title that must not be compared',1300,520)]
    if field: rows.append(item('Comment as Example Page',1400,850))
    return rows


@pytest.fixture
def context(monkeypatch):
    clock = Clock()
    monkeypatch.setattr('modules.live_profile_comment.time.monotonic',clock.clock)
    monkeypatch.setattr('modules.live_profile_comment.time.sleep',clock.sleep)
    task = Mock()
    task.client.screenshot.return_value = np.zeros((1080,1920,3),dtype=np.uint8)
    task.same.side_effect = lambda a,b:a['center']==b['center']
    state = {'items':profile_items()}
    def read(screen, region=None, **kwargs):
        if region is None: return []
        x,y,w,h = region
        return [i for i in state['items'] if x <= i['bounds'][0] < x+w and y <= i['bounds'][1] < y+h]
    task.vision.read_text.side_effect = read
    return task,state,clock


@pytest.mark.parametrize('age',['Just now','30s','1m','2m','3m'])
def test_recent_first_post_needs_no_caption_or_title_match(context,age):
    task,state,_ = context
    state['items'] = profile_items(age)
    task.title = 'Entirely unrelated configured title'
    items,target = LiveProfileComment(task).find(70)
    assert target['center'] == (1400,850)
    assert 'live_profile_recent_post_verified' in [c.args[0] for c in task.report.call_args_list]
    task.live_input.human.navigate.assert_called_once_with('https://www.facebook.com/me')
    task.live_input.human.key_press.assert_called_once_with('ctrl+home',check=False)
    task.live_input.human.click.assert_not_called()


@pytest.mark.parametrize('age',['4m','10m','1h','yesterday','unreadable'])
def test_old_or_unreadable_first_post_is_not_skipped_to_find_another(context,age):
    task,state,_ = context
    state['items'] = profile_items(age)
    with pytest.raises(LiveCommentFailure,match='profile_post_not_recent'):
        LiveProfileComment(task).find(70)
    task.live_input.human.scroll.assert_not_called()
    task.live_input.enter.assert_not_called()


def test_missing_comment_input_cannot_scroll_into_next_post(context):
    task,state,_ = context
    state['items'] = profile_items(field=False)
    task.live_input.human.scroll.side_effect = lambda *a,**k:state.update(items=[
        item('Example Page',1120,500),item('2m',1120,525),item('Comment as Example Page',1400,850)])
    with pytest.raises(LiveCommentFailure,match='profile_input_missing'):
        LiveProfileComment(task).find(70)
    task.live_input.human.scroll.assert_called_once()
    task.live_input.enter.assert_not_called()


def test_timestamp_first_fallback_then_single_confirmed_comment(context):
    task,state,_ = context
    task.capture.return_value.image = task.client.screenshot.return_value
    original_read = task.vision.read_text.side_effect
    # Producer has no input; profile navigation reveals the first card.
    on_profile = [False]
    task.vision.read_text.side_effect = lambda *a,**k:original_read(*a,**k) if on_profile[0] else []
    task.live_input.human.navigate.side_effect = lambda url:on_profile.__setitem__(0,True)
    def key(name,**kwargs):
        if name == 'Return':state['items'].append(item('https://example.invalid/news',1400,800))
    task.live_input.human.key_press.side_effect = key
    LiveComment(task,after_end=True).post('https://example.invalid/news')
    assert [c.args[0] for c in task.live_input.human.key_press.call_args_list].count('Return') == 1
    task.live_input.enter.assert_called_once()
    assert task.observe_failure_evidence.call_args.args[2] == 'live_profile_comment'


def test_expired_budget_does_not_navigate_or_submit(context):
    task,_,_ = context
    with pytest.raises(LiveCommentFailure):LiveProfileComment(task).find(20)
    task.live_input.human.navigate.assert_not_called()
    task.live_input.enter.assert_not_called()


def test_profile_caption_text_is_not_comment_delivery_proof(context):
    task,state,_ = context
    profile = LiveProfileComment(task)
    items,target = profile.find(70)
    comment = LiveComment(task,after_end=True)
    comment.profile = profile
    items += [item('https://example.invalid/news',1400,520)]
    assert not comment.contains(comment.posted_items(items,target),'https://example.invalid/news')
    items += [item('https://example.invalid/news',1400,800)]
    assert comment.contains(comment.posted_items(items,target),'https://example.invalid/news')
