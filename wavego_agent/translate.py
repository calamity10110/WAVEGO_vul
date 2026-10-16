"""Semantic → native WAVEGO JSON translator.

Every mapping here was verified against the current firmware
(wavego_pro_platformio/WAVEGO_Pro_PlatformIO/src):

- BodyCtrl.cpp:820-827 robotCtrl gait table — T:111 {"FB":fb,"LR":lr} with
  fb/lr in {-1,0,1}: forward=(1,0) 0°, backward=(-1,0) 180°, and the diagonals
  (1,-1)=+30°, (1,1)=-30°, (-1,-1)=+120°, (-1,1)=-120°. LR=-1 is always the
  left component, LR=+1 the right (left turn=(0,-1), right=(0,1)).
- Config.h: CMD_UGV_CTRL=1, CMD_STAND_UP=110, CMD_BASIC_MOVE=111,
  CMD_BASIC_FUNC=112; func 1=stayLow(sit), 3=jump.
- BodyCtrl.cpp:111-112 STEP_DELAY=5 (ms/tick), STEP_ITERATE=0.02 — gait
  cadence, settable via T:115 {"delay":d,"iterate":i}. Smaller delay is
  faster. FB/LR are ints, so normalized speed maps to cadence, not magnitude.
- Stop is T:111 with both zero — byte-identical to the failsafe daemon's
  STOP_CMD (ugv_rpi/failsafe_keepalive.py).

Cadence bounds [SLOW_*, FAST_*] are provisional until verified on hardware;
the defaults at speed 0.5 reproduce firmware defaults exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .schema import Approach, SemanticCommand

# T:111 direction table, straight from BodyCtrl.cpp robotCtrl.
DIRECTION_TO_FB_LR = {
    "forward": (1, 0),
    "backward": (-1, 0),
    "left": (0, -1),
    "right": (0, 1),
    "forward_left": (1, -1),
    "forward_right": (1, 1),
    "backward_left": (-1, -1),
    "backward_right": (-1, 1),
}

STOP_CMD = {"T": 111, "FB": 0, "LR": 0}
SIT_CMD = {"T": 112, "func": 1}
JUMP_CMD = {"T": 112, "func": 3}
STAND_CMD = {"T": 110}

# Cadence interpolation anchors (STEP_DELAY in ms; firmware default 5 at 0.5).
SLOW_DELAY_MS = 8.0
FAST_DELAY_MS = 2.0
STEP_ITERATE = 0.02  # fixed stride increment; firmware default


@dataclass(frozen=True)
class Cadence:
    delay_ms: int
    iterate: float = STEP_ITERATE


DEFAULT_CADENCE = Cadence(delay_ms=5)


def cadence_for_speed(speed: float) -> Cadence:
    """Map normalized speed [0,1] to gait cadence. 0.5 -> firmware defaults.
    Linear in STEP_DELAY across [FAST_DELAY_MS, SLOW_DELAY_MS]."""
    speed = min(1.0, max(0.0, speed))
    delay = SLOW_DELAY_MS + (FAST_DELAY_MS - SLOW_DELAY_MS) * speed
    return Cadence(delay_ms=round(delay))


@dataclass
class TranslatorState:
    """What the firmware's gait parameters are believed to be right now, so
    T:115 is only sent when the cadence actually changes."""

    cadence: Cadence = DEFAULT_CADENCE


def translate(cmd: SemanticCommand, state: TranslatorState | None = None) -> list[dict]:
    """Translate one validated semantic command into the ordered native
    commands to write to the ESP32. Returns [] only for approach, which is
    planned at the behavior layer (see behaviors/supervisor), never sent raw."""
    st = state if state is not None else TranslatorState()
    native: list[dict] = []

    if isinstance(cmd, Approach):
        return []

    if cmd.action in ("walk", "run"):
        fb, lr = DIRECTION_TO_FB_LR[cmd.direction]
        want = cadence_for_speed(cmd.speed)
        if want != st.cadence:
            native.append({"T": 115, "delay": want.delay_ms, "iterate": want.iterate})
            st.cadence = want
        native.append({"T": 111, "FB": fb, "LR": lr})
        return native

    if cmd.action == "turn":
        lr = -1 if cmd.direction == "left" else 1
        native.append({"T": 111, "FB": 0, "LR": lr})
        return native

    if cmd.action == "stop":
        native.append(dict(STOP_CMD))
        return native

    if cmd.action == "stand":
        native.append(dict(STAND_CMD))
        return native

    if cmd.action == "sit":
        native.append(dict(SIT_CMD))
        return native

    if cmd.action == "jump":
        native.append(dict(JUMP_CMD))
        return native

    if cmd.action == "look":
        # cmdPtCtrl reads X/Y only (main.cpp dispatch table); SPD/ACC that
        # base_ctrl.gimbal_ctrl sends are ignored by the firmware for T:133.
        native.append({"T": 133, "X": cmd.pan, "Y": cmd.tilt})
        return native

    raise TypeError(f"unhandled command type: {type(cmd).__name__}")


def reset_cadence(st: TranslatorState) -> TranslatorState:
    """Forget any cadence override (call after stop/stand — the firmware keeps
    its STEP_DELAY, but reverting to defaults keeps AI-driven sessions on the
    verified-safe cadence)."""
    if st.cadence != DEFAULT_CADENCE:
        return replace(st, cadence=DEFAULT_CADENCE)
    return st
