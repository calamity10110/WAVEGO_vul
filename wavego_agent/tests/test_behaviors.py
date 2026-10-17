"""Behavior logic tests — heading wraparound, turn control, approach.

The wraparound cases are the classic yaw bugs: crossing the -180/180 seam,
near-full turns, direction choice for the shortest path.
"""

import pytest

from wavego_agent.behaviors import (
    TurnController,
    TurnDecision,
    approach_step,
    heading_delta,
    normalize_deg,
    plan_approach,
)


# ------------------------------------------------------------ heading math

@pytest.mark.parametrize("raw,expected", [
    (0, 0), (180, 180), (-180, 180), (181, -179), (-181, 179),
    (359, -1), (-359, 1), (540, 180), (720, 0), (179.9, 179.9),
])
def test_normalize_deg(raw, expected):
    assert normalize_deg(raw) == pytest.approx(expected)


@pytest.mark.parametrize("current,target,expected", [
    (170, -170, 20),      # seam crossing: left 20, not right 340
    (-170, 170, -20),     # and the mirror
    (0, 90, 90),
    (90, 0, -90),
    (10, -10, -20),
    (350, 10, 20),        # 350 == -10; 20 left to reach +10
    (0, 0, 0),
    (180, -180, 0),       # same physical heading after wrap
])
def test_heading_delta(current, target, expected):
    assert heading_delta(current, target) == pytest.approx(expected)


# ----------------------------------------------------------- turn control

def feed(controller, yaw):  # convenience
    return controller.update(yaw)


def test_turn_completes_at_target():
    c = TurnController(90)
    assert feed(c, 0).decision is TurnDecision.TURNING
    assert feed(c, 45).decision is TurnDecision.TURNING
    assert feed(c, 88).decision is TurnDecision.DONE


def test_turn_tolerance():
    c = TurnController(90, tolerance_deg=5)
    feed(c, 0)                          # start: target 90
    assert feed(c, 84).decision is TurnDecision.TURNING   # 6 deg left
    assert feed(c, 86).decision is TurnDecision.DONE      # 4 deg left


def test_turn_wraparound_seam():
    c = TurnController(20)
    c.update(170)                       # target wraps to -170
    assert c.update(-178).decision is TurnDecision.TURNING  # 8 deg left
    assert c.update(-172).decision is TurnDecision.DONE     # 2 deg left


def test_turn_negative_relative():
    c = TurnController(-90)
    assert c.update(0).decision is TurnDecision.TURNING
    assert c.update(-95).decision is TurnDecision.DONE


def test_turn_overshoot_flagged_far_from_target():
    c = TurnController(20)
    feed(c, 0)                          # start: target 20
    st = feed(c, 150)                   # blew past: remaining now -130
    assert st.remaining_deg < 0
    assert st.overshoot is True


def test_turn_rejects_out_of_range():
    with pytest.raises(ValueError):
        TurnController(500)
    with pytest.raises(ValueError):
        TurnController(10, tolerance_deg=0)


# -------------------------------------------------------------- approach

def test_approach_turns_toward_target():
    # Bearing convention: positive = target to the left (matches
    # heading_delta and plan_approach). Negative bearing -> turn right.
    right = approach_step(-40, lost_frames=0)
    assert (right.turn_lr, right.walk_fb, right.done) == (1, 0, False)
    left = approach_step(40, lost_frames=0)
    assert (left.turn_lr, left.walk_fb, left.done) == (-1, 0, False)


def test_approach_walks_on_bearing():
    s = approach_step(5, lost_frames=0)
    assert (s.turn_lr, s.walk_fb, s.done) == (0, 1, False)


def test_approach_holds_gait_on_dropped_frame():
    s = approach_step(None, lost_frames=1)
    assert s.done is False and (s.turn_lr, s.walk_fb) == (0, 0)


def test_approach_stops_when_lost_too_long():
    s = approach_step(None, lost_frames=5)
    assert s.done is True


def test_plan_approach_shortest_turn():
    plan = plan_approach(-40, walk_duration_s=3)
    assert plan.turn_direction == "right"
    assert plan.turn_angle_deg == 40
    assert plan.walk_direction == "forward"
    assert plan.walk_duration_s == 3
    assert plan_approach(200).turn_angle_deg <= 180
