import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vlm_ctrl import coherence as H


def test_movement_label_matches_own_direction():
    ok, note = H.coherent("forward", "walking ahead toward the ball")
    assert ok and note == "matched"


def test_movement_label_conflicts_with_other_direction():
    ok, note = H.coherent("forward", "turning left to line up")
    assert not ok and "turn_left" in note


def test_movement_label_generic_reason_passes():
    ok, note = H.coherent("back", "target is behind the robot now")
    assert ok


def test_blocker_negates_conflict():
    ok, note = H.coherent("forward", "path left is blocked, staying straight")
    assert ok


def test_blocker_negates_conflict_cannot():
    ok, note = H.coherent("turn_right", "cannot advance, wall ahead, rotating right")
    assert ok and note == "matched"


def test_stance_label_never_conflicts():
    for label in ("stop", "none", "sit", "stand", "jump"):
        ok, _ = H.coherent(label, "person is walking ahead of me")
        assert ok, label


def test_case_insensitive():
    ok, _ = H.coherent("turn_left", "Turning LEFT now")
    assert ok


def test_stance_label_with_directions_ok():
    ok, _ = H.coherent("stop", "reached the spot on the right side")
    assert ok
