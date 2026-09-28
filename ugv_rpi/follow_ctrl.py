"""Person-follow control policy for WAVEGO Pro.

Pure decision logic, no cv2/numpy/yaml dependencies — runs anywhere and is
unit-testable off-robot. Maps a person detection (normalized x-offset from
frame center and box-height ratio) to discrete T:111 gait commands
(FB/LR in {-1, 0, +1}), per the ESP32 firmware contract:

    FB=1,LR=0   forward      FB=-1,LR=0  backward
    FB=0,LR=-1  turn left    FB=0,LR=1   turn right
    FB=0,LR=0   stop (stand at mass center)

Behavior:
- x-offset beyond +/- deadzone  -> turn toward the target
- x-offset inside deadzone:
      height_ratio < far_ratio  -> walk forward (target far)
      height_ratio > near_ratio -> stop (target too close)
      between the two           -> hold previous move decision (hysteresis band,
                                  prevents stop/forward oscillation at boundary)
- person missing for >= lost_frames consecutive frames -> stop (LOST)
  (before the counter expires the last command is held, riding out single
  dropped detections)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FollowDecision:
    fb: int          # -1 | 0 | 1  forward/backward component of T:111
    lr: int          # -1 | 0 | 1  left/right component of T:111
    state: str       # LOST | STOP | FORWARD | TURN_LEFT | TURN_RIGHT


FORWARD = FollowDecision(1, 0, "FORWARD")
TURN_LEFT = FollowDecision(0, -1, "TURN_LEFT")
TURN_RIGHT = FollowDecision(0, 1, "TURN_RIGHT")
STOP = FollowDecision(0, 0, "STOP")
LOST = FollowDecision(0, 0, "LOST")


class FollowPolicy:

    def __init__(self, deadzone=0.12, near_ratio=0.72, far_ratio=0.45, lost_frames=12):
        if not 0.0 < deadzone < 0.5:
            raise ValueError("deadzone must be in (0, 0.5)")
        if not 0.0 < far_ratio < near_ratio < 1.0:
            raise ValueError("require 0 < far_ratio < near_ratio < 1")
        if lost_frames < 1:
            raise ValueError("lost_frames must be >= 1")
        self.deadzone = deadzone
        self.near_ratio = near_ratio
        self.far_ratio = far_ratio
        self.lost_frames = lost_frames
        self._lost_count = 0
        self._hold = FORWARD        # hysteresis memory for the between-band
        self._last = LOST

    @property
    def last_decision(self):
        return self._last

    def reset(self):
        self._lost_count = 0
        self._hold = FORWARD
        self._last = LOST

    def update(self, detected, x_offset=None, height_ratio=None):
        """Feed one frame's detection result, get the gait decision back.

        detected      -- bool, person found this frame
        x_offset      -- (box_center_x - frame_center_x) / frame_width,
                         negative = target left of center. Required when detected.
        height_ratio  -- box_height / frame_height, bigger = closer.
                         Required when detected.
        """
        if not detected:
            self._lost_count += 1
            if self._lost_count >= self.lost_frames:
                self._last = LOST
            # else: hold last command while the counter rides out dropouts
            return self._last

        if x_offset is None or height_ratio is None:
            raise ValueError("x_offset and height_ratio required when detected")

        self._lost_count = 0
        if x_offset < -self.deadzone:
            self._last = TURN_LEFT
        elif x_offset > self.deadzone:
            self._last = TURN_RIGHT
        elif height_ratio < self.far_ratio:
            self._hold = FORWARD
            self._last = FORWARD
        elif height_ratio > self.near_ratio:
            self._hold = STOP
            self._last = STOP
        else:
            # hysteresis band: keep whatever move/stop decision we last made
            self._last = self._hold
        return self._last
