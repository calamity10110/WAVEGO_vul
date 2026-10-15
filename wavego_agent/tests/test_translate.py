"""Translator tests — every mapping pinned to the verified firmware table.

References: BodyCtrl.cpp:820-827 (gait table), Config.h:38-91 (opcodes),
BodyCtrl.cpp:111 (STEP_DELAY default 5).
"""

from wavego_agent.schema import parse_command
from wavego_agent.translate import (
    DEFAULT_CADENCE,
    cadence_for_speed,
    translate,
    TranslatorState,
)


def t(raw):
    return translate(parse_command(raw))


# ---------------------------------------------------------- gait directions

def test_forward_walk():
    assert t({"action": "walk", "direction": "forward", "speed": 0.5,
              "duration": 3}) == [{"T": 111, "FB": 1, "LR": 0}]


def test_backward_walk():
    assert t({"action": "walk", "direction": "backward", "duration": 1}) \
        == [{"T": 111, "FB": -1, "LR": 0}]


def test_left_and_right_walk():
    assert t({"action": "walk", "direction": "left", "duration": 1}) \
        == [{"T": 111, "FB": 0, "LR": -1}]
    assert t({"action": "walk", "direction": "right", "duration": 1}) \
        == [{"T": 111, "FB": 0, "LR": 1}]


def test_diagonals_match_firmware_gait_table():
    assert t({"action": "walk", "direction": "forward_left", "duration": 1}) \
        == [{"T": 111, "FB": 1, "LR": -1}]    # +30 deg heading
    assert t({"action": "walk", "direction": "forward_right", "duration": 1}) \
        == [{"T": 111, "FB": 1, "LR": 1}]     # -30 deg heading
    assert t({"action": "walk", "direction": "backward_left", "duration": 1}) \
        == [{"T": 111, "FB": -1, "LR": -1}]   # +120 deg heading
    assert t({"action": "walk", "direction": "backward_right", "duration": 1}) \
        == [{"T": 111, "FB": -1, "LR": 1}]    # -120 deg heading


def test_turn_gaits():
    assert t({"action": "turn", "direction": "left", "angle": 30}) \
        == [{"T": 111, "FB": 0, "LR": -1}]
    assert t({"action": "turn", "direction": "right", "duration": 2}) \
        == [{"T": 111, "FB": 0, "LR": 1}]


# ------------------------------------------------------- instant commands

def test_stop_is_the_failsafe_stop():
    assert t({"action": "stop"}) == [{"T": 111, "FB": 0, "LR": 0}]
    from wavego_agent.translate import STOP_CMD as T_STOP
    import ugv_rpi.failsafe_keepalive as fk
    assert T_STOP == fk.STOP_CMD  # single stop shape across the repo


def test_stand_sit_jump_opcodes():
    assert t({"action": "stand"}) == [{"T": 110}]
    assert t({"action": "sit"}) == [{"T": 112, "func": 1}]
    assert t({"action": "jump"}) == [{"T": 112, "func": 3}]


def test_approach_translates_to_nothing():
    assert t({"action": "approach", "target": "redball"}) == []


# ------------------------------------------------------------- cadence

def test_speed_half_is_firmware_default_cadence():
    assert cadence_for_speed(0.5) == DEFAULT_CADENCE
    assert DEFAULT_CADENCE.delay_ms == 5


def test_speed_monotonic_and_bounded():
    delays = [cadence_for_speed(s).delay_ms for s in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert delays == sorted(delays, reverse=True)  # faster speed -> smaller delay
    assert 2 <= min(delays) and max(delays) <= 8


def test_nondefault_speed_emits_cadence_then_gait():
    out = t({"action": "run", "direction": "forward", "speed": 1.0,
             "duration": 2})
    assert out[0] == {"T": 115, "delay": 2, "iterate": 0.02}
    assert out[1] == {"T": 111, "FB": 1, "LR": 0}


def test_cadence_not_resent_when_unchanged():
    st = TranslatorState()
    translate(parse_command({"action": "walk", "direction": "forward",
                             "speed": 1.0, "duration": 1}), st)
    out2 = translate(parse_command({"action": "walk", "direction": "forward",
                                    "speed": 1.0, "duration": 1}), st)
    assert out2 == [{"T": 111, "FB": 1, "LR": 0}]  # no second T:115
    out3 = translate(parse_command({"action": "walk", "direction": "left",
                                    "speed": 0.5, "duration": 1}), st)
    assert out3[0]["T"] == 115  # back toward default cadence -> emit again
