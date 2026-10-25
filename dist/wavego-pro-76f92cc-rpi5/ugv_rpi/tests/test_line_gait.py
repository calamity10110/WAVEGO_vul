import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from follow_ctrl import FollowPolicy, LineGaitPolicy, FORWARD, TURN_LEFT, TURN_RIGHT, LOST

LP = dict(deadzone=0.15, lost_frames=3)


def test_line_centered_forwards():
    p = LineGaitPolicy(**LP)
    assert p.update(True, offset=0.0) == FORWARD
    assert p.update(True, offset=0.1) == FORWARD


def test_line_left_turns_left():
    p = LineGaitPolicy(**LP)
    assert p.update(True, offset=-0.4) == TURN_LEFT


def test_line_right_turns_right():
    p = LineGaitPolicy(**LP)
    assert p.update(True, offset=0.4) == TURN_RIGHT


def test_lost_line_holds_then_stops():
    p = LineGaitPolicy(**LP)
    p.update(True, offset=0.0)
    assert p.update(False).state == 'FORWARD'
    assert p.update(False).state == 'FORWARD'
    assert p.update(False) == LOST


def test_redetect_resets_lost():
    p = LineGaitPolicy(**LP)
    p.update(True, offset=0.0)
    p.update(False)
    p.update(True, offset=0.0)
    assert p.update(False).state == 'FORWARD'
    assert p.update(False).state == 'FORWARD'
    assert p.update(False) == LOST


def test_reset():
    p = LineGaitPolicy(**LP)
    p.update(True, offset=0.5)
    p.reset()
    assert p.last_decision == LOST


def test_offset_required_when_seen():
    try:
        LineGaitPolicy(**LP).update(True)
    except ValueError:
        return
    raise AssertionError('expected ValueError')


def test_invalid_config_raises():
    for bad in (dict(deadzone=0.6), dict(lost_frames=0)):
        try:
            LineGaitPolicy(**{**LP, **bad})
        except ValueError:
            continue
        raise AssertionError(f'expected ValueError for {bad}')


def test_person_policy_still_works():
    p = FollowPolicy(deadzone=0.12, near_ratio=0.72, far_ratio=0.45, lost_frames=3)
    assert p.update(True, x_offset=-0.4, height_ratio=0.3) == TURN_LEFT
