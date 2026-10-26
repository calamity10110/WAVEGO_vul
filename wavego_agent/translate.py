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

# Gait engine (T:117) — mode codes and heading angles from GaitEngine.h /
# BodyCtrl.cpp robotCtrl. Heading sign matches the firmware gait table:
# positive = travel toward the LEFT. "left"/"right" are turn-in-place and
# have no heading — engine commands with those directions fall back to the
# legacy T:111 turn path.
GAIT_MODE_TO_T = {"crawl": 0, "walk": 1, "run": 2}
DIRECTION_TO_HEADING = {
    "forward": 0,
    "forward_left": 30,
    "forward_right": -30,
    "backward_left": 120,
    "backward_right": -120,
    "backward": 180,
}

# Cadence interpolation anchors (STEP_DELAY in ms; firmware default 5 at 0.5).
SLOW_DELAY_MS = 8.0
FAST_DELAY_MS = 2.0
STEP_ITERATE = 0.02  # fixed stride increment; firmware default


@dataclass(frozen=True)
class Cadence:
    delay_ms: int
    iterate: float = STEP_ITERATE


DEFAULT_CADENCE = Cadence(delay_ms=5)

# Body-lean (T:133) — BodyCtrl::ptCtrl maps the wire values to body degrees:
#   X in [-180, 180] -> body yaw  in [-30, 30]    (mapDouble(X, -180, 180, -30, 30))
#   Y in [-30, 30]   -> body pitch in [-17.5, 17.5] (mapDouble(Y, -30, 30, -17.5, 17.5))
# pitchYawRoll raises the FRONT legs for positive pitch = nose DOWN, so the
# pet-facing "pitch up" negates here. Flip the signs if the mounted camera
# reads the lean opposite once verified on hardware.
BODY_YAW_MAX = 30.0
BODY_PITCH_MAX = 17.5
LEAN_YAW_SIGN = 1.0
LEAN_PITCH_SIGN = -1.0


def lean_native(pitch: float, yaw: float) -> dict:
    """Body-degree lean -> T:133 wire values (exact inverse of the
    firmware's mapDouble scalings)."""
    x = (yaw / BODY_YAW_MAX) * 180.0 * LEAN_YAW_SIGN
    y = (pitch / BODY_PITCH_MAX) * 30.0 * LEAN_PITCH_SIGN
    return {"T": 133, "X": round(x, 2), "Y": round(y, 2)}


def cadence_for_speed(speed: float) -> Cadence:
    """Map normalized speed [0,1] to gait cadence. 0.5 -> firmware defaults.
    Linear in STEP_DELAY across [FAST_DELAY_MS, SLOW_DELAY_MS]."""
    speed = min(1.0, max(0.0, speed))
    delay = SLOW_DELAY_MS + (FAST_DELAY_MS - SLOW_DELAY_MS) * speed
    return Cadence(delay_ms=round(delay))


@dataclass
class TranslatorState:
    """What the firmware's gait parameters are believed to be right now, so
    T:115 is only sent when the cadence actually changes. ``cadence=None``
    means "the engine (T:117) owns the firmware cadence now" — it is dirty
    until reset_cadence restores the default."""

    cadence: Cadence | None = DEFAULT_CADENCE


def _engine_native(cmd) -> dict | None:
    """Build the T:117 gait-engine command for a walk/run, or None when the
    command uses no engine fields (legacy path) or turns in place (the
    engine has no turn mode in v1 — T:111 keeps that job)."""
    if cmd.gait is None and cmd.height_mm is None and cmd.distance_mm is None:
        return None
    if cmd.direction not in DIRECTION_TO_HEADING:
        return None
    mode = cmd.gait or ("run" if cmd.action == "run" else "walk")
    native = {"T": 117, "mode": GAIT_MODE_TO_T[mode], "speed": cmd.speed,
              "heading": DIRECTION_TO_HEADING[cmd.direction]}
    if cmd.height_mm is not None:
        native["height"] = cmd.height_mm
    if cmd.distance_mm is not None:
        native["distance"] = cmd.distance_mm
    return native


def translate(cmd: SemanticCommand, state: TranslatorState | None = None) -> list[dict]:
    """Translate one validated semantic command into the ordered native
    commands to write to the ESP32. Returns [] only for approach, which is
    planned at the behavior layer (see behaviors/supervisor), never sent raw."""
    st = state if state is not None else TranslatorState()
    native: list[dict] = []

    if isinstance(cmd, Approach):
        return []

    if cmd.action in ("walk", "run"):
        engine = _engine_native(cmd)
        if engine is not None:
            # the engine owns cadence + stride from here; the tracked
            # cadence is dirty until reset_cadence restores T:115 defaults
            st.cadence = None
            native.append(engine)
            return native
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

    if cmd.action == "lean":
        native.append(lean_native(cmd.pitch, cmd.yaw))
        return native

    raise TypeError(f"unhandled command type: {type(cmd).__name__}")


def reset_cadence(st: TranslatorState) -> tuple[TranslatorState, dict | None]:
    """Restore the default gait cadence after a behavior ends.

    Returns (new_state, restore_cmd): restore_cmd is the T:115 native command
    that puts the firmware back on the default cadence, or None when the
    firmware was already there. The firmware keeps its STEP_DELAY across
    behaviors, so the tracked state must not be reset without telling the
    firmware — otherwise the supervisor believes defaults while the robot
    still gaits at the previous cadence.
    """
    if st.cadence != DEFAULT_CADENCE:
        cmd = {"T": 115, "delay": DEFAULT_CADENCE.delay_ms,
               "iterate": DEFAULT_CADENCE.iterate}
        return replace(st, cadence=DEFAULT_CADENCE), cmd
    return st, None
