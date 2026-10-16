"""Only the first post header may authorize Reel profile verification."""
from unittest.mock import Mock, patch
import numpy as np
import pytest
from modules.reel_top_post import first_header_region, evaluate_header
from tasks.facebook_reel import FacebookReelTask


def item(text, x, y, confidence=.95):
    return {'text': text, 'confidence': confidence, 'center': (x+30, y+10), 'bounds': (x, y, x+60, y+20)}


def toolbar():
    return [item('Posts', 1000, 500), item('Manage posts', 1500, 500), item('List view', 1100, 550)]


def header(age):
    return [item('Author Name', 1060, 600), item(age, 1060, 625)]


@pytest.mark.parametrize('age,accepted', [('Just now', True), ('30 seconds ago', True), ('9m', True),
    ('10m', False), ('1 hour ago', False), ('2 days ago', False), ('unknown text', False)])
def test_top_header_only(age, accepted):
    region, seen = first_header_region(toolbar(), 1920, 1080)
    assert seen
    # New timestamps in the body, comment area or next post must never override it.
    items = header(age) + [item('Just now', 1060, 680), item('1m', 1060, 850)]
    assert evaluate_header(items, region, 1080)['accepted'] is accepted


def test_profile_navigation_posts_tab_is_not_a_post_header():
    assert first_header_region([item('Posts', 1000, 500)], 1920, 1080) == (None, False)


def test_low_confidence_and_conflicting_header_are_rejected():
    region, _ = first_header_region(toolbar(), 1920, 1080)
    assert not evaluate_header([header('now')[0], item('now', 1060, 625, .4)], region, 1080)['accepted']
    assert not evaluate_header(header('now') + [item('2 days ago', 1080, 625)], region, 1080)['accepted']


def test_matching_caption_without_new_top_timestamp_is_rejected():
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.caption = 'A caption that matches'
    task.vision = Mock()
    task.log = Mock()
    task.vision.read_text.side_effect = [toolbar(), header('1 hour ago') + [item(task.caption, 1060, 680)]]
    assert not task._latest_reel_is_visible(np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert task._reel_top_post_found


def test_scan_stops_after_old_first_post_without_scrolling_to_second():
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.client = Mock()
    task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    task.vision = Mock()
    task.vision.read_text.side_effect = [toolbar(), header('2 days ago')]
    task.log = Mock()
    task.human = Mock()
    with patch('tasks.facebook_reel.time.sleep'):
        visible, _ = task._scan_profile_for_latest_reel()
    assert not visible
    task.human.scroll.assert_not_called()
    assert task.client.screenshot.call_count == 1


def test_unknown_timestamp_does_not_fall_through_to_nearby_caption():
    region, _ = first_header_region(toolbar(), 1920, 1080)
    items = header('Yesterday') + [item('Just now', 1060, 640)]
    assert not evaluate_header(items, region, 1080)['accepted']


def test_timestamp_after_separate_published_by_attribution_is_parsed():
    region, _ = first_header_region(toolbar(), 1920, 1080)
    for age, accepted in [('9m', True), ('19m', False), ('1 hour ago', False)]:
        rows = [header(age)[0], item('Published by Editor Name', 1060, 625), item(age, 1300, 625)]
        assert evaluate_header(rows, region, 1080)['accepted'] is accepted
