"""Known Meta promotion dismissal must never treat a real post as the card."""
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from modules.meta_feed_promotion import find_meta_feed_promotion, dismiss_meta_feed_promotion


def row(text, x, y):
    return {'text': text, 'confidence': .99, 'bounds': (x, y, x + 250, y + 20)}


def promotion(with_x=True):
    screen = np.full((1080, 1920, 3), 255, np.uint8)
    cv2.circle(screen, (1691, 861), 18, (228, 230, 235), -1)
    if with_x:
        cv2.line(screen, (1685, 855), (1697, 867), (45, 45, 45), 2)
        cv2.line(screen, (1697, 855), (1685, 867), (45, 45, 45), 2)
    items = [row('∞ Meta', 1061, 850), row('Drive more traffic to your website', 1061, 896)]
    return screen, items, (1034, 824, 700, 129)


def test_promotion_requires_visible_circular_x():
    screen, items, region = promotion()
    result = find_meta_feed_promotion(screen, items, region)
    assert result['close_confirmed']
    assert abs(result['center'][0] - 1691) <= 2
    assert abs(result['center'][1] - 861) <= 2
    screen, items, region = promotion(with_x=False)
    assert not find_meta_feed_promotion(screen, items, region)['close_confirmed']


@pytest.mark.parametrize('items', [
    [row('Author', 1061, 850), row('1m', 1061, 896)],
    [row('Author', 1061, 850), row('Sponsored', 1061, 896)],
    [row('Meta', 1061, 850), row('Just now', 1061, 896)],
    [row('Drive more traffic to your website', 1061, 896)],
])
def test_real_post_or_unanchored_copy_is_not_dismissed(items):
    screen, _, region = promotion()
    assert find_meta_feed_promotion(screen, items, region) is None


def test_wrong_card_or_missing_toolbar_is_not_dismissed():
    screen, items, _ = promotion()
    assert find_meta_feed_promotion(screen, items, None) is None
    assert find_meta_feed_promotion(screen, items, (1034, 500, 700, 129)) is None


def test_close_is_bounded_and_not_repeated_on_next_real_post():
    screen, items, region = promotion()
    task = Mock()
    task._meta_feed_promotion_close_attempts = 0
    for _ in range(4):
        assert dismiss_meta_feed_promotion(task, screen, items, region)
    assert task.click_reversible.call_count == 2
    assert not dismiss_meta_feed_promotion(task, screen, [row('Author', 1061, 850)], region)
    assert task.click_reversible.call_count == 2


@pytest.mark.parametrize('kind', ['reel', 'image'])
def test_verifier_recaptures_after_promotion_without_scrolling(kind):
    from unittest.mock import patch
    from tasks.facebook_reel import FacebookReelTask
    from modules.publication_result_verifier import ImagePublicationResultVerifier

    task = FacebookReelTask.__new__(FacebookReelTask)
    task.client = Mock()
    task.human = Mock()
    task.log = Mock()
    first, second = np.zeros((20, 20, 3)), np.ones((20, 20, 3))
    task.client.screenshot.side_effect = [first, second]
    task._latest_reel_is_visible = Mock()
    if kind == 'reel':
        def visible(screen):
            task.latest_reel_timestamp_check = {'reason': 'meta_feed_promotion_present'}
            return screen is second
        task._latest_reel_is_visible.side_effect = visible
        with patch('tasks.facebook_reel.time.sleep'):
            accepted, screen = task._scan_profile_for_latest_reel()
    else:
        verifier = ImagePublicationResultVerifier(task)
        def visible(screen):
            verifier.last_check = {'reason': 'meta_feed_promotion_present'}
            return screen is second
        verifier._post_visible = visible
        with patch('modules.publication_result_verifier.time.sleep'):
            accepted, screen = verifier._scan()
    assert accepted and screen is second
    task.human.scroll.assert_not_called()
    assert task.client.screenshot.call_count == 2
