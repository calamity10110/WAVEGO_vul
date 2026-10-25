"""Robot state and telemetry ingestion.

The supervisor never reads hardware directly — it polls a TelemetrySource.
MockTelemetry drives the simulated pipeline; the Pi-side real source (IMU yaw
from the app's feedback channel, battery voltage from T:1001 frames) plugs in
later without supervisor changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RobotState:
    yaw_deg: float = 0.0
    voltage_v: float | None = None
    moving: bool = False
    # supervisor-clock timestamp of the last fresh feedback frame, or None
    # when the source provides no freshness info (watchdog stays unarmed)
    last_feedback_monotonic: float | None = None


class TelemetrySource(Protocol):

    def poll(self) -> RobotState:
        """Return the current state snapshot. Cheap; called every tick."""
        ...


class MockTelemetry:
    """Scriptable telemetry for tests and desktop simulation."""

    def __init__(self, state: RobotState | None = None):
        self.state = state if state is not None else RobotState()

    def poll(self) -> RobotState:
        return self.state


class StaticYawSource:
    """Pretends the IMU tracks commanded rotation 1:1 — enough to exercise
    TurnController end-to-end in simulation before real IMU fusion exists."""

    def __init__(self, yaw_deg: float = 0.0):
        self.yaw_deg = yaw_deg

    def rotate(self, delta_deg: float) -> None:
        from .behaviors import normalize_deg

        self.yaw_deg = normalize_deg(self.yaw_deg + delta_deg)

    def poll(self) -> RobotState:
        return RobotState(yaw_deg=self.yaw_deg)
