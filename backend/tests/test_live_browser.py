"""Live templates and browser gates without Docker, network or publication."""
import sys
from pathlib import Path
from unittest.mock import Mock
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from engine.brain_runtime import BrainRegistry, BrainResolutionError
from engine.live_brain import pinned_live_brain, resolve_live_brain
from modules.live_producer import LiveProducerTemplates, phrase
from tasks.facebook_live import FacebookLiveTask

ROOT = Path(__file__).resolve().parents[2] / 'automation'
URL = 'https://www.facebook.com/live/producer/'


def item(text, x=100, y=200, confidence=.95):
    return {'text': text, 'center': (x, y), 'bounds': (x-30, y-8, x+30, y+8), 'confidence': confidence}


def image(color=None):
    result = np.zeros((1000, 1200, 3), dtype=np.uint8)
    if color:
        result[910:960, 40:220] = color
    return result


@pytest.fixture
def task(monkeypatch):
    obj = FacebookLiveTask.__new__(FacebookLiveTask)
    obj.package = BrainRegistry(ROOT / 'brains').resolve('facebook_live')
    obj.layouts = LiveProducerTemplates(obj.package)
    obj.client = Mock()
    obj.client.get_current_url.return_value = URL
    obj.vision = Mock()
    obj.click = Mock()
    obj.publish_clicked = False
    obj.state = 'unknown'
    obj.template_id = None
    ticks = iter(range(0, 10000, 10))
    monkeypatch.setattr('tasks.facebook_live.time.monotonic', lambda: next(ticks))
    monkeypatch.setattr('tasks.facebook_live.time.sleep', lambda _: None)
    return obj


def ready(task, enabled=True, duplicate=False):
    buttons = [item('Go live', y=935)]
    if duplicate:
        buttons.append(item('Go live', x=250, y=935))
    return task.layouts.observe(image((255, 100, 8) if enabled else None), {
        'sidebar': [item('Create live video')], 'footer': buttons,
        'setup_center': [item('Streaming software setup'), item('Stream key', y=300)],
        'details': [item('Edit Post Details')],
    })


def active(task, confidence=.58):
    return task.layouts.observe(image((100, 100, 255)), {
        'sidebar': [item('Live dashboard'), item('Flash News Today is live now', y=300)],
        'footer': [item('End live video', y=935, confidence=confidence)],
    })


def ended(task):
    return task.layouts.observe(image(), {
        'sidebar': [item('Your live video has', y=200), item('ended', y=225)],
        'footer': [item('Return home', y=935), item('Live video ended', y=975)],
    })


def test_start_confirms_low_confidence_end_control_after_one_click(task):
    button = ready(task)
    screens = iter([button, button, active(task)])
    task.capture = lambda _: next(screens)
    task.start()
    task.click.assert_called_once_with(button.regions['footer'][0])
    assert task.state == 'live'


def test_disabled_or_ambiguous_button_never_clicks(task):
    for frame in (ready(task, enabled=False), ready(task, duplicate=True)):
        task.capture = lambda _: frame
        with pytest.raises(RuntimeError, match='did not confirm'):
            task.start()
    task.click.assert_not_called()


def test_start_never_repeats_unconfirmed_publish(task):
    task.capture = lambda _: ready(task)
    with pytest.raises(RuntimeError, match='did not confirm'):
        task.start()
    assert task.click.call_count == 1


def test_stale_template_blocks_publish(task):
    count = 0
    def capture(_):
        nonlocal count
        count += 1
        return ready(task, enabled=count % 2 == 1)
    task.capture = capture
    with pytest.raises(RuntimeError):
        task.start()
    task.click.assert_not_called()


def test_auto_started_live_requires_no_click(task):
    task.capture = lambda _: active(task)
    task.start()
    task.click.assert_not_called()


@pytest.mark.parametrize('header', ['Your live video has', 'Your live video'])
def test_end_accepts_wrapped_ended_header(task, header):
    frame = task.layouts.observe(image(), {
        'sidebar': [item(header, y=200), item('ended', y=225)],
        'footer': [item('Return home', y=935), item('Live video ended', y=975)],
    })
    assert frame.template_id == 'broadcast_ended'
    task.capture = lambda _: frame
    task.end()
    assert task.state == 'ended'
    task.click.assert_not_called()


def test_end_requires_real_ended_confirmation_and_one_end_click(task):
    task.capture = lambda _: active(task)
    task.vision.read_text.return_value = []
    with pytest.raises(RuntimeError, match='did not confirm'):
        task.end()
    assert task.click.call_count == 1


@pytest.mark.parametrize('panel', ['left', 'center'])
def test_prepare_recognizes_configured_setup_layouts(task, panel):
    frame = task.layouts.observe(image(), {
        'setup_' + panel: [item('Streaming software setup'), item('Stream key', y=300)],
        'details': [item('Edit Post Details')],
    })
    assert frame.template_id == 'setup_' + panel
    task.capture = lambda _: frame
    task.prepare(URL)
    assert task.state == 'prepared'


def test_prepare_rejects_missing_details_before_ingest(task):
    frame = task.layouts.observe(image(), {'setup_center': [item('Streaming software setup'), item('Stream key', y=300)]})
    task.capture = lambda _: frame
    with pytest.raises(RuntimeError, match='required title and description'):
        task.prepare(URL)
    task.click.assert_not_called()


def test_prepare_does_not_combine_different_setup_panel_labels(task):
    frame = task.layouts.observe(image(), {
        'setup_left': [item('Stream key')], 'setup_center': [item('Streaming software setup')],
        'details': [item('Edit Post Details')],
    })
    assert frame.template_id is None


def test_prepare_opens_home_using_clear_welcome_heading(task):
    button = item('Set up live video', x=900, y=740)
    home = task.layouts.observe(image(), {'home': [item('Welcome back Flash News Today'), button]})
    assert home.template_id == 'producer_home'
    screens = iter([home, home, ready(task)])
    task.capture = lambda _: next(screens)
    task.prepare(URL)
    task.click.assert_called_once_with(button)


def test_prepare_selects_source_card_above_label(task):
    label = item('Streaming software', x=900, y=333)
    source = task.layouts.observe(image(), {'setup_center': [item('Select a video source'), label]})
    assert source.template_id == 'source_selector'
    screens = iter([source, source, ready(task)])
    task.capture = lambda _: next(screens)
    task.prepare(URL)
    task.click.assert_called_once_with(dict(label, center=(900, 285)))


def test_prepare_refuses_active_or_ended_broadcast(task):
    for frame in (active(task), ended(task)):
        task.capture = lambda _: frame
        with pytest.raises(RuntimeError):
            task.prepare(URL)
    task.click.assert_not_called()


def test_live_notice_without_dashboard_and_red_end_control_is_unknown(task):
    frame = task.layouts.observe(image(), {'sidebar': [item('is live now')], 'footer': [item('End live video', y=935)]})
    assert frame.template_id is None


def test_red_end_button_without_live_notice_is_not_publication_confirmation(task):
    frame = active(task)
    frame.regions['sidebar'] = [item('Live dashboard')]
    assert task.layouts.observe(frame.image, frame.regions).template_id is None


def test_phrase_does_not_join_distant_labels():
    assert not phrase([item('Your live video has', y=200), item('ended', y=800)], 'your live video has ended')


def test_control_capture_excludes_video_and_key_panels(task):
    task.client.screenshot.return_value = image()
    task.vision.read_text.return_value = []
    task.capture('start')
    regions = [call.kwargs['region'] for call in task.vision.read_text.call_args_list]
    assert len(regions) == 3
    assert all(x + width <= 1200 * .22 + 1 for x, y, width, height in regions)


def test_workflow_run_returns_only_safe_metadata(task):
    task.capture = lambda _: active(task)
    result = task.run('start', URL)
    assert result['state'] == 'live'
    assert result['template_id'] == 'live_dashboard'
    assert result['brain']['id'] == 'facebook_live'
    assert set(result) == {'success', 'state', 'template_id', 'brain'}


def test_workflow_refuses_actions_outside_facebook(task):
    task.client.get_current_url.return_value = 'https://example.com/live/producer/'
    with pytest.raises(RuntimeError, match='Keep Facebook'):
        task.run('start', URL)
    task.click.assert_not_called()


def test_bundled_fallback_and_digest_pin_are_enforced(tmp_path):
    package = resolve_live_brain(ROOT, tmp_path)
    assert pinned_live_brain(package.root, package.digest).metadata() == package.metadata()
    with pytest.raises(RuntimeError, match='changed'):
        pinned_live_brain(package.root, '0' * 64)
    installed = tmp_path / 'brains' / 'facebook_live' / 'bundled_default'
    installed.mkdir(parents=True)
    with pytest.raises(BrainResolutionError):
        resolve_live_brain(ROOT, tmp_path)  # Invalid installed versions must not silently fall back.


def test_installed_workflow_is_visible_to_brain_manager_and_not_overwritten(tmp_path):
    from engine.live_brain import install_bundled_live_brain
    from brain_cli import list_brains
    assert install_bundled_live_brain(ROOT, tmp_path)
    family = next(b for b in list_brains(tmp_path / 'brains')['brains'] if b['id'] == 'facebook_live')
    assert family['active_version'] == 'bundled_default'
    assert family['versions'][0]['status'] == 'valid'
    installed = tmp_path / 'brains' / 'facebook_live' / 'bundled_default' / 'manifest.json'
    original = installed.read_bytes()
    assert not install_bundled_live_brain(ROOT, tmp_path)
    assert installed.read_bytes() == original


def test_prepare_fills_supplied_details_before_accepting_setup(task, monkeypatch):
    frame = ready(task)
    task.title, task.caption = 'News today', 'News caption'
    task.capture = lambda _: frame
    fill = Mock()
    monkeypatch.setattr('tasks.facebook_live.LivePostDetails', lambda _: Mock(fill=fill))
    task.prepare(URL)
    fill.assert_called_once_with(frame, 'News today', 'News caption')
    assert task.state == 'prepared'
    task.click.assert_not_called()


def test_post_details_verifies_both_fields_then_fresh_save(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    frame = ready(task)
    task.capture = lambda _: frame
    task.client.exec_cmd.return_value.stdout = 'News title'
    labels = [item('Title', x=600, y=450), item('Description', x=600, y=550), item('Save', x=600, y=740)]
    screen = image()
    screen[720:760, 540:660] = (255, 100, 8)
    details.read_dialog = lambda: (screen, labels)
    copies = iter(['News title', 'Caption\nខ្មែរ 😀'])
    def execute(args):
        return Mock(stdout=next(copies)) if args[0] == 'timeout' else Mock(stdout='')
    task.client.exec_cmd.side_effect = execute
    details.fill(frame, 'News title', 'Caption\nខ្មែរ 😀')
    commands = [call.args[0] for call in task.client.exec_cmd.call_args_list]
    assert ['xdotool', 'type', '--clearmodifiers', '--', 'News title'] in commands
    assert ['xdotool', 'type', '--clearmodifiers', '--', 'Caption\nខ្មែរ 😀'] in commands
    assert task.click.call_args.args[0]['text'] == 'Save'
    assert not any(call.args[0].get('text') == 'Go live' for call in task.click.call_args_list)


def test_post_details_blocks_save_after_failed_field_verification(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    details.read_dialog = lambda: (image(), [item('Add a title', x=600, y=450)])
    task.client.exec_cmd.return_value.stdout = 'wrong field'
    with pytest.raises(RuntimeError, match='did not accept'):
        details.field(details.TITLE, 'Correct title')
    assert task.click.call_count == 1


def test_post_details_blocks_stale_open_and_disabled_save(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    frame = ready(task)
    task.capture = lambda _: active(task)
    with pytest.raises(RuntimeError, match='Could not open'):
        details.fill(frame, 'Title', 'Caption')
    task.click.assert_not_called()
    task.capture = lambda _: frame
    details.field = Mock()
    details.read_dialog = lambda: (image(), [item('Title'), item('Description'), item('Save')])
    with pytest.raises(RuntimeError, match='not enabled Save'):
        details.fill(frame, 'Title', 'Caption')
    assert task.click.call_count == 1  # Only Edit post details; no Save/Go live.


def test_post_details_uses_clickable_prompt_instead_of_section_heading():
    from modules.live_post_details import LivePostDetails
    labels = [item('Add post details', x=1200, y=190),
              item("What's your live video about?", x=1323, y=237, confidence=.73)]
    targets = LivePostDetails.find(labels, LivePostDetails.OPEN)
    assert len(targets) == 1
    assert targets[0]['text'] == "What's your live video about?"


def test_required_labels_are_inside_the_live_inputs(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    title = item('Title (required)', x=764, y=464)
    details.read_dialog = lambda: (image(), [title])
    task.client.exec_cmd.return_value.stdout = 'trump'
    details.field(details.TITLE, 'trump')
    task.click.assert_called_once_with(title)


def test_live_post_form_uses_narrow_modal_region(task):
    from modules.live_post_details import LivePostDetails
    task.client.screenshot.return_value = image()
    LivePostDetails(task).read_dialog()
    assert task.vision.read_text.call_args.kwargs['region'] == (420, 250, 360, 600)


def test_description_required_label_sits_above_editable_body(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    caption = item('Description (required)', x=774, y=506)
    details.read_dialog = lambda: (image(), [caption])
    task.client.exec_cmd.return_value.stdout = 'trump think'
    details.field(details.CAPTION, 'trump think')
    assert task.click.call_args.args[0]['center'] == (774, 534)


def test_prepare_accepts_fresh_saved_frame_without_repeating_ocr(task, monkeypatch):
    frame = ready(task)
    task.title, task.caption = 'News title', 'News caption'
    task.capture = Mock(return_value=frame)
    monkeypatch.setattr('tasks.facebook_live.LivePostDetails', lambda _: Mock(fill=Mock(return_value=frame)))
    task.prepare(URL)
    task.capture.assert_called_once_with('prepare')
    assert task.state == 'prepared'
    task.click.assert_not_called()


def test_saved_caption_has_no_placeholder_but_body_is_anchored_to_fresh_form(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    title = item('Title (required)', x=752, y=453)
    labels = [item('Create post'), title, item('Save', y=856)]
    details.read_dialog = lambda: (image(), labels)
    task.client.exec_cmd.return_value.stdout = 'trump think'
    details.field(details.CAPTION, 'trump think')
    assert task.click.call_args.args[0]['center'] == (752, 533)


def test_saved_caption_anchor_requires_confirmed_post_form(task):
    from modules.live_post_details import LivePostDetails
    details = LivePostDetails(task)
    details.read_dialog = lambda: (image(), [item('Title (required)')])
    with pytest.raises(RuntimeError, match='Could not identify'):
        details.field(details.CAPTION, 'Caption')
    task.click.assert_not_called()


def test_scrolled_producer_home_is_recognized_without_welcome_heading(task):
    frame = task.layouts.observe(image(), {'sidebar': [item('Home')], 'home': [
        item('Set up live video', x=950, y=550), item('Past live videos', x=860, y=650), item('Live now', x=990, y=650)]})
    assert frame.template_id == 'producer_home'
    assert task.layouts.permits(frame, 'click_candidate', target='open_setup')


def test_home_fallback_requires_sidebar_and_both_history_tabs(task):
    frame = task.layouts.observe(image(), {'home': [item('Set up live video'), item('Live now')]})
    assert frame.template_id is None


@pytest.mark.parametrize('enabled', [True, False])
def test_focused_footer_text_still_requires_enabled_button_and_producer_state(task, enabled):
    task.client.screenshot.return_value = image((255, 100, 8) if enabled else None)
    task.vision.read_text.side_effect = [[item('Create live video')], [item('Back', y=955)],
                                        [item('Go live', x=160, y=955)]]
    frame = task.capture('start')
    assert (frame.template_id == 'broadcast_ready') is enabled
    assert task.vision.read_text.call_args.kwargs['region'] == (126, 940, 66, 50)


def test_end_iive_ocr_variant_confirms_only_with_live_dashboard_notice_and_red_control(task):
    frame = active(task)
    frame.regions['footer'][0]['text'] = 'End Iive video'
    observed = task.layouts.observe(frame.image, frame.regions)
    assert observed.template_id == 'live_dashboard'
    assert task.layouts.permits(observed, 'verify_publication', state='live')
    observed = task.layouts.observe(image(), frame.regions)
    assert observed.template_id is None


@pytest.mark.parametrize('enabled', [False, True])
def test_preset_switch_requires_unique_knob_and_state_color(enabled):
    import cv2
    from modules.live_pinned_comment import find_switch
    screen = np.zeros((1080,1920,3),dtype=np.uint8)
    anchor = item('Enable preset pinned comment',x=1280,y=800)
    screen[786:815,1553:1607] = (255,100,8) if enabled else (120,120,120)
    cv2.circle(screen,(1593 if enabled else 1567,800),12,(255,255,255),-1)
    result = find_switch(screen,anchor)
    assert result['enabled'] is enabled
    cv2.circle(screen,(1500,800),12,(255,255,255),-1)
    assert find_switch(screen,anchor) is None


def test_blank_preset_disables_without_opening_or_typing(task):
    from modules.live_pinned_comment import LivePinnedComment
    module = LivePinnedComment(task)
    module.set_enabled = Mock()
    module.open_editor = Mock()
    module.restore_setup = Mock(return_value=ready(task))
    module.set(ready(task),'')
    module.set_enabled.assert_called_once_with(False)
    module.open_editor.assert_not_called()
    task.client.exec_cmd.assert_not_called()


def test_changed_preset_switch_never_clicks(task):
    from modules.live_pinned_comment import LivePinnedComment
    module = LivePinnedComment(task)
    module.settings = lambda: (image(),[],{'center':(100,100),'enabled':False})
    module.read_right = lambda: (image(),[])
    module.toggle = lambda *_: {'center':(160,100),'enabled':False}
    with pytest.raises(RuntimeError,match='moved'):
        module.set_enabled(True)
    task.click.assert_not_called()


def test_preset_clipboard_mismatch_blocks_save(task):
    from modules.live_pinned_comment import LivePinnedComment
    module = LivePinnedComment(task)
    module.set_enabled = Mock()
    module.open_editor = Mock()
    module.focus_body = Mock()
    module.copy_body = lambda: 'different text'
    with pytest.raises(RuntimeError,match='did not accept'):
        module.set(ready(task),'Comment')
    task.click.assert_not_called()


def test_prepare_applies_comment_before_returning_prepared(task,monkeypatch):
    frame = ready(task)
    module = Mock()
    module.set.return_value = frame
    monkeypatch.setattr('tasks.facebook_live.LivePinnedComment',lambda _:module)
    task.pinned_comment = 'Pinned message'
    task.capture = lambda _:frame
    task.prepare(URL)
    module.set.assert_called_once_with(frame,'Pinned message')
    assert task.state == 'prepared'


def test_preset_edit_accepts_enabled_blue_text_link_but_rejects_dim_text():
    from modules.live_pinned_comment import LivePinnedComment
    link=item('Edit',x=500,y=500)
    screen=image()
    x1,y1,x2,y2=link['bounds']
    screen[y1:y2,x1:x2]=(255,182,117)
    assert LivePinnedComment.enabled_edit(screen,link)
    screen[y1:y2,x1:x2]=(90,90,90)
    assert not LivePinnedComment.enabled_edit(screen,link)
