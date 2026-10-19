"""Passive plans are bounded, coherent and never contain public actions."""
from unittest.mock import patch
from engine.behavior_profile import BehaviorSession
from engine.passive_browsing import BrowseStep, PassiveBrowsing


def planner():
    return PassiveBrowsing(BehaviorSession('medium', 1234, 'same-attempt'), 1920, 1080)


def test_toolbar_pointer_always_moves_into_content():
    plan = planner()
    x, y = plan.pointer_target((960, 61))
    left, top, right, bottom = plan.bounds
    assert left <= x <= right and top <= y <= bottom
    assert y > 100


def test_pointer_normally_rests_and_drift_is_small():
    plan = planner()
    point = (1100, 650)
    with patch.object(plan.mouse_rng, 'random', return_value=.9):
        assert plan.pointer_target(point) is None
    with patch.object(plan.mouse_rng, 'random', return_value=.1):
        x, y = plan.pointer_target(point)
        assert abs(x-point[0]) <= (plan.bounds[2]-plan.bounds[0])*.20 + 1
        assert abs(y-point[1]) <= (plan.bounds[3]-plan.bounds[1])*.20 + 1


def test_first_burst_enters_content_even_when_pointer_is_already_inside():
    assert planner().pointer_target((1100, 650), first=True) is not None


def test_no_rereading_upwards_at_start_and_all_pauses_are_bounded():
    plan = planner()
    for _ in range(100):
        step = plan.next_step()
        assert step.direction == 'down'
        assert 1 <= step.notches <= 4
        assert 1 <= step.pause_seconds <= 8


def test_reread_is_short_and_cannot_go_above_the_start():
    plan = planner()
    plan.record(BrowseStep('down', 1, 2, 'browse'), changed=True)
    with patch.object(plan.rng, 'random', return_value=0):
        step = plan.next_step()
    assert step.reason == 'reread'
    assert step.direction == 'up' and step.notches == 1
    plan.record(step, changed=True)
    assert plan.net_down == 0
    assert plan.next_step().direction == 'down'


def test_unchanged_page_gets_one_boundary_check_then_stops():
    plan = planner()
    down = BrowseStep('down', 3, 2, 'browse')
    plan.record(down, changed=False)
    plan.record(down, changed=False)
    recovery = plan.next_step()
    assert recovery.reason == 'check_scroll_boundary' and recovery.direction == 'up'
    plan.record(recovery, changed=False)
    plan.record(down, changed=False)
    assert plan.next_step() is None
    assert plan.stopped_reason == 'view_unchanged'


def test_reproducible_plans_have_independent_mouse_and_scroll_streams():
    first, second = planner(), planner()
    for _ in range(5):
        first.pointer_target((960, 61))
    assert first.next_step() == second.next_step()
