"""Caption layout and review regressions, with no browser or input dispatch."""
from unittest.mock import Mock

import numpy as np
import pytest

from modules.composer_entry import EntryResult, CaptionReviewFailure
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask


def box(text, x, y):
    return dict(text=text, center=(x, y), confidence=.99)


def sidebar():
    return [box('Create post', 123, 174), box('People', 60, 234),
            box('Location', 173, 234), box('Fundraiser', 461, 234),
            box("What's on your mind?", 100, 282), box('Uploaded media', 100, 388)]


def photo_task(reads):
    task = FacebookPostTask.__new__(FacebookPostTask)
    task.client = Mock()
    task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    task.vision = Mock()
    task.vision.get_pixel_region.return_value = (345, 32, 1228, 885)
    task.vision.find_stable.side_effect = lambda locate, **kwargs: locate()
    task.vision.read_text.side_effect = [[], reads]
    task.record_locator_telemetry = Mock()
    return task


def test_sidebar_from_saved_account_16_coordinates():
    task = photo_task(sidebar())
    assert task._stable_caption_target() == (100, 282)
    assert task.vision.read_text.call_args.kwargs['region'] == (0, 140, 729, 345)


@pytest.mark.parametrize('missing', ['Create post', 'Uploaded media', 'controls'])
def test_sidebar_requires_local_composer_context(missing):
    records = [i for i in sidebar() if i['text'] != missing]
    if missing == 'controls':
        records = [i for i in records if i['text'] not in {'People', 'Location', 'Fundraiser'}]
    assert photo_task(records)._stable_caption_target() is None


def test_sidebar_rejects_placeholder_from_right_preview():
    records = sidebar()
    records[4] = box("What's on your mind?", 1200, 282)
    assert photo_task(records)._stable_caption_target() is None


def reel_task():
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.client = Mock()
    task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    task.vision = Mock()
    task.vision.find_stable.side_effect = lambda locate, **kwargs: locate()
    task.log = Mock()
    task.capture_evidence = Mock()
    task._reel_description_target = (95, 278)
    return task


def test_cached_description_does_not_confirm_empty_field():
    task = reel_task()
    task.vision.find_text_cascaded.return_value = None
    task.vision.read_text.return_value = []
    assert task._find_reel_description_target() == (95, 278)
    assert task._find_reel_description_target(require_placeholder=True) is None
    # The Uploaded media geometry fallback cannot prove an empty draft.
    assert task.vision.find_text_cascaded.call_count == 1


def test_fresh_placeholder_can_confirm_empty_field():
    task = reel_task()
    task.vision.find_text_cascaded.return_value = box('Describe your reel...', 95, 278)
    assert task._find_reel_description_target(require_placeholder=True) == (95, 278)


def test_caption_disappearing_during_review_is_not_pasted_again():
    task = reel_task()
    task.caption = 'requested caption'
    task.video_path = 'video.mp4'
    task._wait_for_target = Mock(return_value=('ready', (95, 278), task.client.screenshot()))
    task.composer_entry = Mock()
    task.composer_entry.enter.return_value = EntryResult('entered_verified', True, 'clipboard_exact')
    task.review_composer = Mock(side_effect=CaptionReviewFailure('caption_empty_after_review'))
    status, screen = task._enter_reel_caption(timeout=15)
    assert status == 'review_failed'
    task.composer_entry.enter.assert_called_once()
    assert not getattr(task, '_reel_caption_entered', False)
    details = task.capture_evidence.call_args.kwargs
    assert details['phase'] == 'review'
    assert details['reason'] == 'caption_empty_after_review'
    assert details['entry_verified'] is True
