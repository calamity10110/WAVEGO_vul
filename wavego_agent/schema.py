"""Semantic command schema for the WAVEGO agent.

The VLM (or any planner) emits semantic JSON like
``{"action": "walk", "direction": "forward", "speed": 0.4, "duration": 3}`` —
never native WAVEGO protocol fields. This module is the strict boundary:
anything that is not exactly one of the known actions with exactly the known
fields in range is rejected before it can reach a translator.

Speed is normalized command intensity in [0, 1] — never metres per second.
Turn commands take exactly one of ``angle`` (degrees) or ``duration``
(seconds), never both, never neither.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

MAX_DURATION_S = 60.0
MIN_DURATION_S = 0.1
MAX_ANGLE_DEG = 360.0
MIN_ANGLE_DEG = 1.0

Direction8 = Literal[
    "forward",
    "backward",
    "left",
    "right",
    "forward_left",
    "forward_right",
    "backward_left",
    "backward_right",
]
TurnDirection = Literal["left", "right"]


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    speed: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5


class Walk(_Command):
    action: Literal["walk"]
    direction: Direction8
    duration: Annotated[float, Field(ge=MIN_DURATION_S, le=MAX_DURATION_S)]


class Run(_Command):
    action: Literal["run"]
    direction: Direction8
    duration: Annotated[float, Field(ge=MIN_DURATION_S, le=MAX_DURATION_S)]


class Turn(_Command):
    action: Literal["turn"]
    direction: TurnDirection
    angle: Annotated[float, Field(ge=MIN_ANGLE_DEG, le=MAX_ANGLE_DEG)] | None = None
    duration: Annotated[float, Field(ge=MIN_DURATION_S, le=MAX_DURATION_S)] | None = None

    @model_validator(mode="after")
    def _exactly_one_of_angle_duration(self) -> "Turn":
        if (self.angle is None) == (self.duration is None):
            raise ValueError("turn requires exactly one of 'angle' or 'duration'")
        return self


class Jump(_Command):
    action: Literal["jump"]


class Stand(_Command):
    action: Literal["stand"]


class Sit(_Command):
    action: Literal["sit"]


class Stop(_Command):
    action: Literal["stop"]


class Approach(_Command):
    action: Literal["approach"]
    target: Annotated[str, Field(min_length=1, max_length=64)]


SemanticCommand = Annotated[
    Union[Walk, Run, Turn, Jump, Stand, Sit, Stop, Approach],
    Field(discriminator="action"),
]

_ADAPTER = TypeAdapter(SemanticCommand)


class CommandRejected(ValueError):
    """A semantic command failed validation. Carries a human-readable reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_command(raw: Union[str, dict]) -> SemanticCommand:
    """Validate one semantic command (dict, or JSON string as a VLM would
    emit). Raises CommandRejected with the first actionable problem — never
    lets a malformed command through."""
    try:
        if isinstance(raw, str):
            return _ADAPTER.validate_json(raw)
        return _ADAPTER.validate_python(raw)
    except ValidationError as exc:
        # first error is enough for an operator log; the full list is noise
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err.get("loc", ()) or ("input",))
        raise CommandRejected(f"{loc}: {err['msg']}") from exc
