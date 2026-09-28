"""Tests for follow_ctrl.FollowPolicy — pure logic, runs off-robot.

Run:  python -m pytest tests/test_follow_policy.py
  or: python tests/test_follow_policy.py   (plain-assert fallback, no pytest)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from follow_ctrl import FollowPolicy, FORWARD, TURN_LEFT, TURN_RIGHT, STOP, LOST

P = dict(deadzone=0.12, near_ratio=0.72, far_ratio=0.45, lost_frames=3)


def test_turn_left_when_target_left():
    p = FollowPolicy(**P)
    assert p.update(True, x_offset=-0.4, height_ratio=0.3) == TURN_LEFT


def test_turn_right_when_target_right():
    p = FollowPolicy(**P)
    assert p.update(True, x_offset=0.4, height_ratio=0.3) == TURN_RIGHT


def test_deadzone_boundary_is_centered():
    p = FollowPolicy(**P)
    assert p.update(True, x_offset=0.12, height_ratio=0.3) == FORWARD
    assert p.update(True, x_offset=-0.12, height_ratio=0.3) == FORWARD


def test_forward_when_far_and_centered():
    p = FollowPolicy(**P)
    assert p.update(True, x_offset=0.0, height_ratio=0.30) == FORWARD


def test_stop_when_too_close():
    p = FollowPolicy(**P)
    assert p.update(True, x_offset=0.0, height_ratio=0.80) == STOP


def test_hysteresis_holds_forward_entering_band_from_far():
    p = FollowPolicy(**P)
    p.update(True, x_offset=0.0, height_ratio=0.30)      # FORWARD, below far_ratio
    assert p.update(True, x_offset=0.0, height_ratio=0.55) == FORWARD   # inside band
    assert p.update(True, x_offset=0.0, height_ratio=0.70) == FORWARD   # still inside band


def test_hysteresis_holds_stop_entering_band_from_near():
    p = FollowPolicy(**P)
    p.update(True, x_offset=0.0, height_ratio=0.80)      # STOP, above near_ratio
    assert p.update(True, x_offset=0.0, height_ratio=0.55) == STOP      # inside band


def test_hysteresis_does_not_leak_across_turn():
    p = FollowPolicy(**P)
    p.update(True, x_offset=0.0, height_ratio=0.80)      # STOP -> _hold = STOP
    assert p.update(True, x_offset=0.5, height_ratio=0.55) == TURN_RIGHT  # turn wins over band


def test_lost_holds_then_stops():
    p = FollowPolicy(**P)                                # lost_frames = 3
    p.update(True, x_offset=0.0, height_ratio=0.30)      # FORWARD
    assert p.update(False).state == "FORWARD"            # 1st miss: hold
    assert p.update(False).state == "FORWARD"            # 2nd miss: hold
    assert p.update(False) == LOST                       # 3rd miss: stop


def test_lost_stays_stopped():
    p = FollowPolicy(**P)
    for _ in range(10):
        assert p.update(False) == LOST


def test_redetect_resets_lost_counter():
    p = FollowPolicy(**P)
    p.update(True, x_offset=0.0, height_ratio=0.30)
    p.update(False)
    p.update(False)
    p.update(True, x_offset=0.0, height_ratio=0.30)      # re-acquired before expiry
    assert p.update(False).state == "FORWARD"            # miss 1 after reacquire: counter restarted
    assert p.update(False).state == "FORWARD"            # miss 2: still holding
    assert p.update(False) == LOST                       # miss 3: counter restarted, so exactly 3 to LOST


def test_stop_commands_are_always_zero():
    p = FollowPolicy(**P)
    for d in (p.update(True, 0.0, 0.9), p.update(False), p.update(False), p.update(False)):
        if d.state in ("STOP", "LOST"):
            assert (d.fb, d.lr) == (0, 0)


def test_reset_returns_to_lost():
    p = FollowPolicy(**P)
    p.update(True, x_offset=-0.5, height_ratio=0.30)
    p.reset()
    assert p.last_decision == LOST
    assert p.update(False) == LOST


def test_turn_left_uses_negative_lr():
    p = FollowPolicy(**P)
    d = p.update(True, x_offset=-0.3, height_ratio=0.3)
    assert (d.fb, d.lr) == (0, -1)


def test_invalid_config_raises():
    for bad in (
        dict(deadzone=0.6), dict(deadzone=0.0),
        dict(near_ratio=0.4, far_ratio=0.5),   # crossing thresholds
        dict(lost_frames=0),
    ):
        try:
            FollowPolicy(**{**P, **bad})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")


def test_missing_measurements_raise():
    p = FollowPolicy(**P)
    for args in ((True, None, 0.3), (True, 0.0, None)):
        try:
            p.update(*args)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {args}")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} FAILURES'}")
    sys.exit(1 if failures else 0)
