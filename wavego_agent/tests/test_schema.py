"""Schema tests — the strict boundary in front of the robot.

Everything the spec calls out: unknown fields rejected, invalid ranges
rejected, hardware-level commands rejected (they're simply not actions),
turn takes angle XOR duration, speed is 0..1.
"""

import pytest

from wavego_agent.schema import CommandRejected, parse_command


# ------------------------------------------------------------- happy path

def test_walk_full_form():
    cmd = parse_command({"action": "walk", "direction": "forward",
                         "speed": 0.4, "duration": 3})
    assert cmd.action == "walk"
    assert cmd.direction == "forward"
    assert cmd.speed == 0.4
    assert cmd.duration == 3


def test_walk_speed_defaults_to_half():
    cmd = parse_command({"action": "walk", "direction": "left", "duration": 1})
    assert cmd.speed == 0.5


def test_json_string_input():
    cmd = parse_command('{"action": "turn", "direction": "left", "angle": 30}')
    assert cmd.angle == 30
    assert cmd.duration is None


def test_turn_by_duration():
    cmd = parse_command({"action": "turn", "direction": "right", "duration": 2})
    assert cmd.angle is None
    assert cmd.duration == 2


def test_all_instant_actions():
    for action in ("jump", "stand", "sit", "stop"):
        assert parse_command({"action": action}).action == action


def test_approach():
    cmd = parse_command({"action": "approach", "target": "redball"})
    assert cmd.target == "redball"


def test_all_diagonal_directions_accepted():
    for d in ("forward_left", "forward_right", "backward_left", "backward_right",
              "forward", "backward", "left", "right"):
        parse_command({"action": "walk", "direction": d, "duration": 1})


# ----------------------------------------------------------- rejections

def test_unknown_action_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "dance"})


def test_unknown_field_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "forward",
                       "duration": 1, "T": 111})


def test_native_protocol_field_rejected_even_alone():
    with pytest.raises(CommandRejected):
        parse_command({"T": 111, "FB": 1, "LR": 0})


def test_speed_above_one_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "forward",
                       "speed": 1.5, "duration": 1})


def test_negative_speed_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "forward",
                       "speed": -0.1, "duration": 1})


def test_turn_with_both_angle_and_duration_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "turn", "direction": "left",
                       "angle": 30, "duration": 2})


def test_turn_with_neither_angle_nor_duration_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "turn", "direction": "left"})


def test_turn_angle_bounds():
    for bad in (0, 361, -30):
        with pytest.raises(CommandRejected):
            parse_command({"action": "turn", "direction": "left", "angle": bad})
    parse_command({"action": "turn", "direction": "left", "angle": 360})


def test_duration_bounds():
    for bad in (0, 0.05, 61):
        with pytest.raises(CommandRejected):
            parse_command({"action": "walk", "direction": "forward",
                           "duration": bad})


def test_bogus_direction_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "sideways", "duration": 1})


def test_empty_approach_target_rejected():
    with pytest.raises(CommandRejected):
        parse_command({"action": "approach", "target": ""})


def test_rejection_message_names_the_field():
    with pytest.raises(CommandRejected) as ei:
        parse_command({"action": "walk", "direction": "forward",
                       "duration": 1, "gait": "trot"})
    assert "gait" in str(ei.value)


def test_non_dict_input_rejected():
    with pytest.raises(CommandRejected):
        parse_command("walk forward please")


def test_look_ranges_enforced():
    from wavego_agent.schema import parse_command as pc
    assert pc({"action": "look", "pan": 180, "tilt": -30}).tilt == -30
    with pytest.raises(CommandRejected):
        pc({"action": "look", "pan": 181})
    with pytest.raises(CommandRejected):
        pc({"action": "look", "tilt": 31})
