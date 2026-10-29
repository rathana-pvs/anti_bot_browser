"""Regressions for stale profile content during navigation and passive fallback."""
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import cv2
import numpy as np
import pytest

from modules.reel_profile_readiness import browser_navigation_state, wait_for_profile_ready
from modules.publication_result_verifier import ReelPublicationResultVerifier
from modules.reel_gallery_verifier import reels_tab, viewer_identity, ReelGalleryVerifier
from tasks.facebook_reel import FacebookReelTask


@pytest.fixture
def clock(monkeypatch):
    ticks = [0.]
    monkeypatch.setattr('time.monotonic', lambda: ticks[0])
    monkeypatch.setattr('time.sleep', lambda duration: ticks.__setitem__(0, ticks[0] + duration))
    return ticks


def item(text, x=1100, y=400, confidence=.99):
    return dict(text=text, bounds=(x, y, x+100, y+20), center=(x+50, y+10), confidence=confidence)


def test_browser_stop_cross_is_loading_and_unknown_toolbar_is_not_idle():
    screen = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    assert browser_navigation_state(screen) == 'unknown'
    cv2.line(screen, (90, 58), (99, 67), (80,)*3, 1, cv2.LINE_AA)
    cv2.line(screen, (99, 58), (90, 67), (80,)*3, 1, cv2.LINE_AA)
    assert browser_navigation_state(screen) == 'loading'


@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
@pytest.mark.parametrize('dark', [False, True])
def test_saved_chrome_reload_is_idle_at_supported_scales(scale, dark):
    # Only the 16x15 toolbar icon is retained; no account or page content.
    icon = cv2.imread(str(Path(__file__).parent / 'fixtures' / 'chrome_reload.png'))
    assert icon is not None
    screen = np.full((round(1080*scale), round(1920*scale), 3), 255, dtype=np.uint8)
    x, y, w, h = (round(n*scale) for n in (87, 55, 16, 15))
    screen[y:y+h, x:x+w] = cv2.resize(icon, (w, h))
    if dark:
        screen = 255 - screen
    assert browser_navigation_state(screen) == 'idle'


def test_saved_reload_allows_stable_profile_check(clock):
    task = task_stub()
    screen = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    screen[55:70, 87:103] = cv2.imread(str(Path(__file__).parent / 'fixtures' / 'chrome_reload.png'))
    task.client.screenshot.return_value = screen
    task.vision.read_text.return_value = [item('Posts'), item('Reels')]
    assert wait_for_profile_ready(task, timeout=5)
    assert clock[0] == 2
    assert task.reel_profile_readiness['reason'] == 'profile_ready'


def task_stub():
    return SimpleNamespace(client=Mock(), vision=Mock(), log=Mock(), capture_evidence=Mock())


def test_stable_old_feed_is_not_ready_while_browser_is_loading(clock, monkeypatch):
    task = task_stub()
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    monkeypatch.setattr('modules.reel_profile_readiness.browser_navigation_state', lambda _: 'loading')
    assert not wait_for_profile_ready(task, timeout=5)
    assert clock[0] == 5
    task.vision.read_text.assert_not_called()
    assert task.reel_profile_readiness['reason'] == 'profile_navigation_loading'


def test_navigation_must_finish_then_settle_before_reading_profile(clock, monkeypatch):
    task = task_stub()
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    task.vision.read_text.return_value = [item('Posts'), item('Reels')]
    monkeypatch.setattr('modules.reel_profile_readiness.browser_navigation_state',
                        lambda _: 'loading' if clock[0] < 4 else 'idle')
    assert wait_for_profile_ready(task, timeout=10)
    assert clock[0] == 6
    assert task.reel_profile_readiness['accepted']


def test_idle_login_page_is_not_a_ready_profile(clock, monkeypatch):
    task = task_stub()
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    task.vision.read_text.return_value = [item('Log in')]
    monkeypatch.setattr('modules.reel_profile_readiness.browser_navigation_state', lambda _: 'idle')
    assert not wait_for_profile_ready(task, timeout=5)
    assert task.reel_profile_readiness['reason'] == 'profile_not_visible'


def test_old_card_updates_in_place_without_refresh_or_second_post(clock):
    task = task_stub()
    screen = object()
    task.client.screenshot.return_value = screen
    def read(_):
        recent = clock[0] >= 6
        task.latest_reel_timestamp_check = {'reason': 'recent_top_post' if recent else 'old_top_post'}
        return recent
    task._latest_reel_is_visible = Mock(side_effect=read)
    task.latest_reel_timestamp_check = {'reason': 'old_top_post'}
    assert ReelPublicationResultVerifier(task)._poll_old_first_post(screen) == (True, screen)
    assert clock[0] == 6
    task.client.exec_cmd.assert_not_called()


def test_old_card_poll_is_bounded(clock):
    task = task_stub()
    task._latest_reel_is_visible = Mock(return_value=False)
    task.latest_reel_timestamp_check = {'reason': 'old_top_post'}
    assert not ReelPublicationResultVerifier(task)._poll_old_first_post(object())[0]
    assert clock[0] == 15


def test_unsettled_navigation_never_scans_or_uses_gallery():
    task = Mock()
    task._allow_reel_profile_navigation.return_value = True
    task._wait_for_reel_profile_ready.return_value = False
    task.reel_profile_readiness = {'accepted': False, 'reason': 'profile_navigation_loading'}
    assert not ReelPublicationResultVerifier(task).verify_latest_reel()
    assert task.navigate_to.call_count == 3
    task._scan_profile_for_latest_reel.assert_not_called()
    task._verify_reel_gallery.assert_not_called()
    task.execute_publish_gate.assert_not_called()


def test_gallery_follows_three_settled_profile_misses():
    task = Mock()
    task._allow_reel_profile_navigation.return_value = True
    task._wait_for_reel_profile_ready.return_value = True
    task.reel_profile_readiness = {'accepted': True}
    task.latest_reel_timestamp_check = {'reason': 'top_header_unreadable'}
    task._scan_profile_for_latest_reel.return_value = (False, object())
    task._verify_reel_gallery.return_value = True
    assert ReelPublicationResultVerifier(task).verify_latest_reel()
    assert task.navigate_to.call_count == 3
    task._verify_reel_gallery.assert_called_once()
    task.execute_publish_gate.assert_not_called()


def test_reels_tab_needs_profile_navigation_peers():
    tab = item('Reels', 700, 400)
    assert reels_tab([tab, item('Reel', 1400, 540)], 1080) is None
    assert reels_tab([tab, item('All', 540, 400), item('Photos', 800, 400)], 1080) is tab
    assert reels_tab([tab, item('All', 540, 500), item('Photos', 800, 500)], 1080) is None


@pytest.mark.parametrize('age,author,accepted', [('1m', 'Page Name', True), ('4h', 'Page Name', False),
                                               ('1m', 'Other Page', False), ('unknown', 'Page Name', False)])
def test_viewer_proof_binds_author_and_recent_header(age, author, accepted):
    caption = 'Specific caption identifying this particular video'
    items = [item(author), item(age, y=425), item(caption, y=460)]
    check = viewer_identity(items, 'Page Name', caption, 1080)
    assert check['accepted'] is accepted
    if accepted:
        assert check['caption_matched']


def test_recent_comment_or_conflicting_timestamp_cannot_confirm_old_reel():
    items = [item('Page Name'), item('4h', y=425), item('Just now', y=700)]
    assert not viewer_identity(items, 'Page Name', 'caption', 1080)['accepted']
    items.append(item('1m', x=1230, y=425))
    assert not viewer_identity(items, 'Page Name', 'caption', 1080)['accepted']


def test_caption_timestamp_cannot_replace_missing_viewer_header():
    assert not viewer_identity([item('Page Name'), item('Just now', y=460)],
                               'Page Name', 'caption', 1080)['accepted']


def test_matching_generic_words_do_not_confirm_viewer_caption():
    items = [item('Page Name'), item('1m', y=425), item('Watch closely breaking news', y=460)]
    check = viewer_identity(items, 'Page Name', 'Watch closely this is a different video entirely', 1080)
    assert check['accepted'] and not check['caption_matched']


def test_gallery_media_miss_cannot_open_arbitrary_tile(clock, monkeypatch):
    task = Mock()
    task._reel_profile_author = 'Page Name'
    task.video_path = '/missing.mp4'
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    tab = item('Reels', 700, 400)
    task.vision.read_text.return_value = [tab, item('All', 540, 400), item('Photos', 800, 400)]
    monkeypatch.setattr('modules.reel_gallery_verifier.browser_navigation_state', lambda _: 'idle')
    monkeypatch.setattr('modules.reel_gallery_verifier.wait_for_profile_ready', lambda *a, **k: True)
    verifier = ReelGalleryVerifier(task)
    verifier.media._matching_image_bounds = Mock(return_value=None)
    assert not verifier.verify()
    task.human.click.assert_called_once_with(*tab['center'])
    assert task.reel_gallery_check['reason'] == 'reel_media_not_matched'
    task.execute_publish_gate.assert_not_called()


@pytest.mark.parametrize('age,caption_matches,url_valid,expected', [
    ('1m', True, True, True), ('4h', True, True, False),
    ('1m', False, True, False), ('1m', True, False, False),
])
def test_gallery_confirmation_requires_recent_matching_viewer_and_reel_url(
        clock, monkeypatch, age, caption_matches, url_valid, expected):
    task = task_stub()
    task._reel_profile_author = 'Page Name'
    task._allow_reel_profile_navigation = Mock(return_value=True)
    task.validate_facebook_permalink = Mock(return_value=(url_valid, 'https://www.facebook.com/reel/123'))
    task.video_path = '/video.mp4'
    task.caption = 'Specific words that identify this particular uploaded video'
    task.human = Mock()
    task.execute_publish_gate = Mock()
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    tab = item('Reels', 700, 400)
    navigation = [tab, item('All', 540, 400), item('Photos', 800, 400)]
    viewer = [item('Page Name'), item(age, y=425),
              item(task.caption if caption_matches else 'Completely different story', y=460)]
    task.vision.read_text.side_effect = lambda *a, **k: navigation if task.human.click.call_count < 2 else viewer
    monkeypatch.setattr('modules.reel_gallery_verifier.browser_navigation_state', lambda _: 'idle')
    monkeypatch.setattr('modules.reel_gallery_verifier.wait_for_profile_ready', lambda *a, **k: True)
    verifier = ReelGalleryVerifier(task)
    # The grid matches twice. The playing viewer need not show the cover frame;
    # its caption must then match instead, with owner and recency still required.
    matches = iter([(1100, 550, 1400, 1000), (1100, 550, 1400, 1000)])
    verifier.media._matching_image_bounds = Mock(side_effect=lambda *a, **k: next(matches, None))
    assert verifier.verify() is expected
    assert task.human.click.call_count == 2  # Reels tab, then the matching tile.
    task.execute_publish_gate.assert_not_called()
    if expected:
        assert task._verified_reel_gallery_permalink['post_url'] == 'https://www.facebook.com/reel/123'
    else:
        assert not hasattr(task, '_verified_reel_gallery_permalink')


def test_gallery_tile_moving_between_observations_blocks_open(clock, monkeypatch):
    task = task_stub()
    task._reel_profile_author = 'Page Name'
    task._allow_reel_profile_navigation = Mock(return_value=True)
    task.human = Mock()
    task.video_path = '/video.mp4'
    task.client.screenshot.return_value = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    task.vision.read_text.return_value = [item('Reels', 700, 400), item('All', 540, 400), item('Photos', 800, 400)]
    monkeypatch.setattr('modules.reel_gallery_verifier.browser_navigation_state', lambda _: 'idle')
    monkeypatch.setattr('modules.reel_gallery_verifier.wait_for_profile_ready', lambda *a, **k: True)
    verifier = ReelGalleryVerifier(task)
    verifier.media._matching_image_bounds = Mock(side_effect=[(1100, 550, 1400, 1000), (1200, 550, 1500, 1000)])
    assert not verifier.verify()
    assert task.human.click.call_count == 1
    assert task.reel_gallery_check['reason'] == 'reel_media_changed'


def test_gallery_confirmed_comment_uses_exact_permalink():
    task = FacebookReelTask.__new__(FacebookReelTask)
    task._verified_reel_gallery_permalink = {'post_url': 'https://www.facebook.com/reel/123'}
    task.post_first_comment = Mock(return_value='submitted_verified')
    task._refresh_until_latest_reel_visible = Mock()
    status, result = task.post_reel_first_comment_after_refresh('https://example.com')
    assert status == 'submitted_verified'
    assert result['post_url'] == 'https://www.facebook.com/reel/123'
    task._refresh_until_latest_reel_visible.assert_not_called()
    task.post_first_comment.assert_called_once_with('https://example.com', post_url=result['post_url'],
                                                   prefer_permalink=True, warm_down_after_submit=False)


def test_gallery_discovered_during_comment_verification_uses_exact_permalink():
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.post_first_comment = Mock(return_value='submitted_verified')
    def verify():
        task._verified_reel_gallery_permalink = {'post_url': 'https://www.facebook.com/reel/456'}
        return True
    task._refresh_until_latest_reel_visible = Mock(side_effect=verify)
    status, result = task.post_reel_first_comment_after_refresh('https://example.com')
    assert status == 'submitted_verified'
    task.post_first_comment.assert_called_once_with('https://example.com', post_url=result['post_url'],
                                                   prefer_permalink=True, warm_down_after_submit=False)


def test_gallery_permalink_is_preserved_in_publication_result():
    from modules.publishing_pipeline import PublicationResultVerifierModule
    task = Mock()
    task.result_status = 'pending_profile_verification'
    task._refresh_until_latest_reel_visible.return_value = True
    task._verified_reel_gallery_permalink = {'post_url': 'https://www.facebook.com/reel/123',
                                            'permalink_status': 'captured'}
    result = PublicationResultVerifierModule(task, 'reel').run(Mock())
    assert result.outputs['post_url'] == 'https://www.facebook.com/reel/123'
    assert result.outputs['publication_status'] == 'published'
    assert task.result_status == 'published'
