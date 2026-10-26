"""Translator tests — every mapping pinned to the verified firmware table.

References: BodyCtrl.cpp:820-827 (gait table), Config.h:38-91 (opcodes),
BodyCtrl.cpp:111 (STEP_DELAY default 5).
"""

from wavego_agent.schema import parse_command
from wavego_agent.translate import (
    DEFAULT_CADENCE,
    cadence_for_speed,
    reset_cadence,
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


def test_reset_cadence_emits_firmware_restore():
    # After a fast behavior the firmware still holds the fast STEP_DELAY;
    # reset must hand back a T:115 that actually restores defaults.
    st = TranslatorState()
    translate(parse_command({"action": "run", "direction": "forward",
                             "speed": 1.0, "duration": 1}), st)
    assert st.cadence != DEFAULT_CADENCE
    st2, restore = reset_cadence(st)
    assert st2.cadence == DEFAULT_CADENCE
    assert restore == {"T": 115, "delay": DEFAULT_CADENCE.delay_ms,
                       "iterate": DEFAULT_CADENCE.iterate}


def test_reset_cadence_noop_when_already_default():
    st = TranslatorState()
    st2, restore = reset_cadence(st)
    assert restore is None
    assert st2.cadence == DEFAULT_CADENCE


# ------------------------------------------------------------------- lean

def test_lean_uses_firmware_body_degree_ranges():
    # full-scale lean hits the exact mapDouble corner values
    assert t({"action": "lean", "pitch": 17.5, "yaw": -30}) == [
        {"T": 133, "X": -180.0, "Y": -30.0}]


def test_lean_pitch_up_negates_firmware_sign():
    # firmware +pitch = nose down; pet pitch +10 = nose up -> Y negative
    assert t({"action": "lean", "pitch": 10.0}) == [
        {"T": 133, "X": 0.0, "Y": -17.14}]


def test_lean_defaults_level():
    assert t({"action": "lean"}) == [{"T": 133, "X": 0.0, "Y": 0.0}]


# ------------------------------------------------------------- gait engine
# T:117 — any of gait/height_mm/distance_mm switches walk/run onto the
# parametric engine; mode codes and headings mirror GaitEngine.h.

def test_engine_walk_with_gait_field():
    out = t({"action": "walk", "direction": "forward", "speed": 0.5,
             "duration": 3, "gait": "crawl"})
    assert out == [{"T": 117, "mode": 0, "speed": 0.5, "heading": 0}]


def test_engine_run_defaults_to_run_mode():
    out = t({"action": "run", "direction": "forward", "speed": 0.8,
             "duration": 2, "distance_mm": 300})
    assert out == [{"T": 117, "mode": 2, "speed": 0.8, "heading": 0,
                    "distance": 300}]


def test_engine_walk_defaults_to_walk_mode():
    out = t({"action": "walk", "direction": "backward", "speed": 0.3,
             "duration": 2, "height_mm": 85})
    assert out == [{"T": 117, "mode": 1, "speed": 0.3, "heading": 180,
                    "height": 85}]


def test_engine_diagonal_headings_match_gait_table():
    # same angles the legacy FB/LR table encodes: +left, -right
    assert t({"action": "walk", "direction": "forward_left", "duration": 1,
              "gait": "walk"})[0]["heading"] == 30
    assert t({"action": "walk", "direction": "forward_right", "duration": 1,
              "gait": "walk"})[0]["heading"] == -30
    assert t({"action": "walk", "direction": "backward_left", "duration": 1,
              "gait": "walk"})[0]["heading"] == 120
    assert t({"action": "walk", "direction": "backward_right", "duration": 1,
              "gait": "walk"})[0]["heading"] == -120


def test_engine_turn_in_place_falls_back_to_legacy():
    # engine has no turn mode in v1 — T:111 keeps that job
    assert t({"action": "walk", "direction": "left", "duration": 1,
              "gait": "crawl"}) == [{"T": 111, "FB": 0, "LR": -1}]
    assert t({"action": "run", "direction": "right", "duration": 1,
              "distance_mm": 100}) == [{"T": 111, "FB": 0, "LR": 1}]


def test_engine_command_marks_cadence_dirty():
    st = TranslatorState()
    out = translate(parse_command({"action": "walk", "direction": "forward",
                                   "duration": 1, "gait": "crawl"}), st)
    assert out[0]["T"] == 117
    assert st.cadence is None               # engine owns firmware cadence now
    st2, restore = reset_cadence(st)        # supervisor runs this after the behavior
    assert restore == {"T": 115, "delay": 5, "iterate": 0.02}
    assert st2.cadence == DEFAULT_CADENCE


def test_engine_rejects_out_of_bounds():
    import pytest
    from wavego_agent.schema import CommandRejected
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "forward", "duration": 1,
                       "height_mm": 111})   # above WALK_HEIGHT_MAX
    with pytest.raises(CommandRejected):
        parse_command({"action": "run", "direction": "forward", "duration": 1,
                       "distance_mm": 0})   # must be positive
    with pytest.raises(CommandRejected):
        parse_command({"action": "walk", "direction": "forward", "duration": 1,
                       "gait": "sprint"})   # unknown style
