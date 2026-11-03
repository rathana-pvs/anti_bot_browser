"""The FB44 profile's 'Personal blog' + 'Insights' is not a login prompt."""
from unittest.mock import Mock

import numpy as np
import pytest

from engine.screen_state import FacebookStateRecognizer, ScreenState
from modules.reel_post_click_verifier import ReelPostClickVerifier
from tasks.facebook_reel import FacebookReelTask


@pytest.mark.parametrize('method', ['observe', 'observe_session_gate', 'observe_safety_gate'])
def test_fb44_profile_labels_do_not_match_login(method):
    vision = Mock()
    vision.read_text.return_value = [
        {'text': 'Manage Page'}, {'text': 'Personal blog'}, {'text': 'Insights'},
        {'text': "What's on your mind?"}, {'text': 'Photo/video'},
    ]
    vision.find_blue_action_buttons.return_value = []
    vision.find_blue_action_button.return_value = None
    recognizer = FacebookStateRecognizer(vision)
    observation = getattr(recognizer, method)(np.zeros((1080, 1920, 3), np.uint8))
    assert observation.state != ScreenState.LOGIN_REQUIRED
    if method != 'observe_safety_gate':
        assert observation.state == ScreenState.FEED_READY


@pytest.mark.parametrize('label', ['Log in', 'LOG IN', 'Log   in', 'Forgot password?', 'Create new account'])
@pytest.mark.parametrize('method', ['observe', 'observe_session_gate', 'observe_safety_gate'])
def test_real_login_prompts_still_block(label, method):
    vision = Mock()
    vision.read_text.return_value = [{'text': label}]
    observation = getattr(FacebookStateRecognizer(vision), method)(np.zeros((1080, 1920, 3), np.uint8))
    assert observation.state == ScreenState.LOGIN_REQUIRED


def test_fb44_closed_profile_allows_comment_navigation_without_republishing():
    screen = np.zeros((1080, 1920, 3), np.uint8)
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.client = Mock()
    task.client.screenshot.return_value = screen
    task.vision = Mock()
    task.vision.find_blue_action_buttons.return_value = []
    task.vision.find_blue_action_button.return_value = None
    task.vision.read_text.return_value = [
        {'text': 'Manage Page'}, {'text': 'Personal blog'}, {'text': 'Insights'},
        {'text': "What's on your mind?"}, {'text': 'Photo/video'},
    ]
    task.recognizer = FacebookStateRecognizer(task.vision)
    task._uncertain = Mock()
    task.execute_publish_gate = Mock()
    assert ReelPostClickVerifier(task).allow_profile_navigation()
    task._uncertain.assert_not_called()
    task.execute_publish_gate.assert_not_called()
