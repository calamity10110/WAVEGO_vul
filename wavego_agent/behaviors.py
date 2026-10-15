"""Pure behavior logic — heading math, turn control, approach planning.

No hardware, no I/O: everything here takes state in and returns decisions,
so it is unit-testable off-robot (the follow_ctrl.py pattern). The motion
supervisor consumes these decisions and does the actual transport writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


def normalize_deg(angle: float) -> float:
    """Wrap any heading to (-180, 180]."""
    wrapped = (angle + 180.0) % 360.0 - 180.0
    if wrapped == -180.0:
        return 180.0
    return wrapped


def heading_delta(current_deg: float, target_deg: float) -> float:
    """Shortest signed rotation from current to target, in (-180, 180].
    Positive = turn left (counter-clockwise). Wraparound-safe: from 170 to
    -170 the answer is +20, not -340."""
    return normalize_deg(target_deg - current_deg)


class TurnDecision(Enum):
    TURNING = "turning"
    DONE = "done"


@dataclass(frozen=True)
class TurnState:
    decision: TurnDecision
    remaining_deg: float   # shortest signed rotation still needed
    overshoot: bool        # crossed the target between updates


class TurnController:
    """Closes the loop on a relative turn using IMU yaw feedback.

    Feed update(yaw) every telemetry tick; it says whether to keep the turn
    gait running or issue the stop. direction/sign of the gait is fixed at
    construction (the supervisor translated the command already); this class
    only decides *when to stop*, handling yaw wraparound.
    """

    def __init__(self, relative_angle_deg: float, tolerance_deg: float = 5.0):
        if not -360.0 <= relative_angle_deg <= 360.0:
            raise ValueError("relative_angle_deg out of [-360, 360]")
        if tolerance_deg <= 0:
            raise ValueError("tolerance_deg must be positive")
        self.target_yaw: float | None = None
        self.remaining_deg = relative_angle_deg
        self.tolerance_deg = tolerance_deg
        self._started = False

    def update(self, yaw_deg: float) -> TurnState:
        if not self._started:
            self._started = True
            self.target_yaw = normalize_deg(yaw_deg + self.remaining_deg)
        remaining = heading_delta(yaw_deg, self.target_yaw)  # type: ignore[arg-type]
        self.remaining_deg = remaining
        done = abs(remaining) <= self.tolerance_deg
        # overshoot: the sign flipped and we are not within tolerance yet —
        # stop rather than orbiting around the target forever
        return TurnState(
            decision=TurnDecision.DONE if done else TurnDecision.TURNING,
            remaining_deg=remaining,
            overshoot=(not done) and abs(self.remaining_deg) > 90.0,
        )


@dataclass(frozen=True)
class ApproachStep:
    """One closed-loop correction toward a target held at a camera bearing."""

    turn_lr: int      # -1 left, +1 right, 0 none (gait LR component)
    walk_fb: int      # 1 forward while roughly on-bearing, 0 otherwise
    done: bool
    reason: str


BEARING_TOLERANCE_DEG = 10.0


def approach_step(bearing_deg: float | None, lost_frames: int,
                  max_lost_frames: int = 5) -> ApproachStep:
    """VLM/vision feed: bearing of the target relative to the robot's heading
    (None when not detected this frame). Turn toward it, walk when roughly
    aimed, stop when the target is lost for too long. Bounded correction:
    while turning we do not walk (no simultaneous gait in the firmware)."""
    if lost_frames >= max_lost_frames:
        return ApproachStep(0, 0, True, f"target lost {lost_frames} frames")
    if bearing_deg is None:
        return ApproachStep(0, 0, False, "hold last gait, frame dropped")
    if bearing_deg < -BEARING_TOLERANCE_DEG:
        return ApproachStep(-1, 0, False, "turn left toward target")
    if bearing_deg > BEARING_TOLERANCE_DEG:
        return ApproachStep(1, 0, False, "turn right toward target")
    return ApproachStep(0, 1, False, "on bearing, walking")


@dataclass(frozen=True)
class ApproachPlan:
    """Open-loop v1 plan: aim, then walk. The supervisor refines it with
    approach_step corrections while the target stays visible."""

    turn_direction: str          # "left" | "right"
    turn_angle_deg: float        # in [0, 180]
    walk_direction: str          # "forward"
    walk_duration_s: float


def plan_approach(bearing_deg: float, walk_duration_s: float = 2.0) -> ApproachPlan:
    """Aim at the target's camera bearing (clamped to the shortest turn the
    robot can express, i.e. <= 180°) then walk at it."""

    wrapped = normalize_deg(bearing_deg)
    if wrapped > 0:
        turn_dir, angle = "left", wrapped
    else:
        turn_dir, angle = "right", -wrapped
    return ApproachPlan(
        turn_direction=turn_dir,
        turn_angle_deg=angle,
        walk_direction="forward",
        walk_duration_s=max(0.1, min(60.0, walk_duration_s)),
    )
