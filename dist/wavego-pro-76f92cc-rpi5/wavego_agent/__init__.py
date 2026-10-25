"""WAVEGO agent — semantic command layer between an AI planner and the robot.

The VLM emits semantic JSON; this package validates it, translates it to the
native WAVEGO protocol, and moves the robot through one motion supervisor
that owns the transport. See README.md for the architecture and the
firmware-verified command mapping table.
"""

from .behaviors import (
    ApproachPlan,
    TurnController,
    TurnDecision,
    approach_step,
    heading_delta,
    normalize_deg,
    plan_approach,
)
from .jsonl import JsonlLogger
from .schema import (
    Approach,
    CommandRejected,
    Jump,
    Run,
    SemanticCommand,
    Sit,
    Stand,
    Stop,
    Turn,
    Walk,
    parse_command,
)
from .supervisor import EStopActive, MotionSupervisor, QueueFullError, Ticket
from .telemetry import MockTelemetry, RobotState, StaticYawSource, TelemetrySource
from .transport import MockTransport, SerialTransport, Transport
from .translate import STOP_CMD, cadence_for_speed, translate, TranslatorState

__all__ = [
    "Approach",
    "ApproachPlan",
    "CommandRejected",
    "EStopActive",
    "Jump",
    "JsonlLogger",
    "MotionSupervisor",
    "MockTelemetry",
    "MockTransport",
    "QueueFullError",
    "RobotState",
    "SerialTransport",
    "Run",
    "STOP_CMD",
    "Sit",
    "Stand",
    "StaticYawSource",
    "Stop",
    "SemanticCommand",
    "TelemetrySource",
    "Ticket",
    "Transport",
    "Turn",
    "TurnController",
    "TurnDecision",
    "TranslatorState",
    "Walk",
    "approach_step",
    "cadence_for_speed",
    "heading_delta",
    "normalize_deg",
    "parse_command",
    "plan_approach",
    "translate",
]
