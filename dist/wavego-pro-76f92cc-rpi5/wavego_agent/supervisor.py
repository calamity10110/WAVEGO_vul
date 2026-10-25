"""Motion supervisor — the single owner of everything that moves the robot.

Design invariants (per the project spec):

- ONE writer: only this supervisor calls Transport.send. The VLM, dashboards
  and vision emit semantic commands; they never touch native JSON.
- The VLM never blocks the loop: commands go through a bounded queue; a full
  queue rejects instead of waiting; emergency stop preempts everything.
- Every behavior is interruptible: preemption replaces the current behavior
  (stop first), emergency stop latches until cleared.
- Safety first: a telemetry watchdog stops motion when feedback goes stale.

The tick loop is injectable-clock and callable by hand (``tick()``), so the
entire lifecycle is testable deterministically without threads or sleeps.
``start()`` runs the same loop in a background thread at ``rate_hz``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from .behaviors import TurnController, TurnDecision, approach_step
from .jsonl import JsonlLogger
from .schema import (
    Approach,
    CommandRejected,
    Run,
    SemanticCommand,
    Walk,
    parse_command,
)
from .telemetry import RobotState, TelemetrySource
from .translate import STOP_CMD, reset_cadence, translate, TranslatorState
from .transport import Transport

log = logging.getLogger("wavego-agent")

APPROACH_MAX_DURATION_S = 60.0
APPROACH_MAX_LOST_FRAMES = 5


class QueueFullError(RuntimeError):
    """The command queue is full — the producer is ahead of the robot."""


class EStopActive(RuntimeError):
    """Emergency stop is latched; new commands are refused."""


@dataclass(frozen=True)
class Ticket:
    id: int
    action: str


@dataclass
class _Entry:
    cmd: SemanticCommand
    preempt: bool = False


# ------------------------------------------------------------- behaviors

class _Context:
    """What a behavior may do: send native commands, read state."""

    def __init__(self, sup: "MotionSupervisor"):
        self._sup = sup

    def send(self, cmd: dict) -> None:
        self._sup._send(cmd)

    @property
    def state(self) -> RobotState:
        return self._sup._last_state


class _Behavior:
    name = "behavior"

    def start(self, ctx: _Context) -> None: ...

    def step(self, ctx: _Context, now: float) -> bool:
        """Advance one tick; True when the behavior is finished."""
        raise NotImplementedError

    def interrupt(self, ctx: _Context) -> None:
        """Best-effort wind-down on preemption/estop."""
        ctx.send(dict(STOP_CMD))


class _Instant(_Behavior):
    """stand / sit / jump / stop — one native command, done."""

    def __init__(self, cmd: SemanticCommand, name: str):
        self._cmd = cmd
        self.name = name

    def start(self, ctx: _Context) -> None:
        for native in translate(self._cmd, self._sup_state(ctx)):
            ctx.send(native)

    def step(self, ctx: _Context, now: float) -> bool:
        return True

    def _sup_state(self, ctx: _Context) -> TranslatorState:
        return ctx._sup._tstate  # noqa: SLF001 — same module, deliberate


class _TimedGait(_Behavior):
    """walk/run — gait at start, stop after duration elapses."""

    def __init__(self, cmd: Walk | Run):
        self._cmd = cmd
        self.name = cmd.action
        self._t0: float | None = None

    def start(self, ctx: _Context) -> None:
        self._t0 = ctx._sup._clock()
        for native in translate(self._cmd, ctx._sup._tstate):
            ctx.send(native)

    def step(self, ctx: _Context, now: float) -> bool:
        if now - self._t0 >= self._cmd.duration:
            ctx.send(dict(STOP_CMD))
            return True
        return False


class _TimedTurn(_Behavior):
    """turn with duration — blind timed rotation, stop at the end."""

    def __init__(self, cmd):
        self._cmd = cmd
        self.name = "turn_timed"
        self._t0: float | None = None

    def start(self, ctx: _Context) -> None:
        self._t0 = ctx._sup._clock()
        for native in translate(self._cmd, ctx._sup._tstate):
            ctx.send(native)

    def step(self, ctx: _Context, now: float) -> bool:
        if now - self._t0 >= self._cmd.duration:
            ctx.send(dict(STOP_CMD))
            return True
        return False


class _YawTurn(_Behavior):
    """turn with angle — gait at start, IMU yaw decides when to stop."""

    def __init__(self, cmd, yaw0: float):
        self._cmd = cmd
        self.name = "turn_yaw"
        rel = cmd.angle if cmd.direction == "left" else -cmd.angle
        self._ctrl = TurnController(rel, tolerance_deg=5.0)
        self._started = False
        self._initial_yaw = yaw0

    def start(self, ctx: _Context) -> None:
        for native in translate(self._cmd, ctx._sup._tstate):
            ctx.send(native)

    def step(self, ctx: _Context, now: float) -> bool:
        ts = self._ctrl.update(ctx.state.yaw_deg)
        if ts.decision is TurnDecision.DONE:
            ctx.send(dict(STOP_CMD))
            return True
        return False


class _Approach(_Behavior):
    """approach — closed loop on vision bearings, no IMU needed.

    aim → walk while on-bearing; every command change is a fresh T:111 write,
    so the firmware's gait table does the direction switching. Bounded by
    APPROACH_MAX_DURATION_S and lost-target frames.
    """

    def __init__(self, cmd: Approach):
        self.target = cmd.target
        self.name = "approach"
        self.bearing: float | None = None
        self.lost_frames = 0
        self._current_gait = (0, 0)
        self._t0: float | None = None

    def start(self, ctx: _Context) -> None:
        self._t0 = ctx._sup._clock()

    def on_bearing(self, bearing: float | None) -> None:
        if bearing is None:
            self.lost_frames += 1
        else:
            self.lost_frames = 0
            self.bearing = bearing

    def step(self, ctx: _Context, now: float) -> bool:
        if now - self._t0 >= APPROACH_MAX_DURATION_S:
            ctx.send(dict(STOP_CMD))
            return True
        if self.lost_frames >= APPROACH_MAX_LOST_FRAMES:
            ctx.send(dict(STOP_CMD))
            return True
        decision = approach_step(self.bearing, self.lost_frames)
        if decision.done:
            ctx.send(dict(STOP_CMD))
            return True
        gait = (decision.walk_fb, decision.turn_lr)
        if gait != self._current_gait:
            ctx.send({"T": 111, "FB": gait[0], "LR": gait[1]})
            self._current_gait = gait
        return False

    def interrupt(self, ctx: _Context) -> None:
        ctx.send(dict(STOP_CMD))


def _behavior_for(cmd: SemanticCommand, yaw0: float | None) -> _Behavior:
    if isinstance(cmd, Approach):
        return _Approach(cmd)
    if isinstance(cmd, Walk | Run):
        return _TimedGait(cmd)
    if cmd.action == "turn":
        if cmd.angle is not None:
            if yaw0 is None:
                raise CommandRejected(
                    "turn by angle needs IMU telemetry (no source configured); "
                    "use duration instead")
            return _YawTurn(cmd, yaw0)
        return _TimedTurn(cmd)
    if cmd.action == "stop":
        return _Instant(cmd, "stop")
    return _Instant(cmd, cmd.action)


# ------------------------------------------------------------- supervisor

class MotionSupervisor:

    def __init__(
        self,
        transport: Transport,
        telemetry: TelemetrySource | None = None,
        logger: JsonlLogger | None = None,
        clock: Callable[[], float] = time.monotonic,
        rate_hz: float = 30.0,
        queue_size: int = 16,
        feedback_timeout_s: float = 2.0,
    ):
        self.transport = transport
        self.telemetry = telemetry
        self.logger = logger or JsonlLogger(None)
        self._clock = clock
        self.rate_hz = rate_hz
        self.feedback_timeout_s = feedback_timeout_s

        self._queue: queue.Queue[_Entry] = queue.Queue(maxsize=queue_size)
        self._estop = threading.Event()
        self._estop_reason: str | None = None
        self._stop_sent_since_estop = False
        self._current: _Behavior | None = None
        self._current_started = False
        self._tstate = TranslatorState()
        self._last_state = RobotState()
        self._ticket_counter = 0
        self._behavior_ctx = _Context(self)
        self._thread: threading.Thread | None = None
        self._running = False

    # ---------------------------------------------------------- public API

    def submit(self, raw: dict | SemanticCommand, preempt: bool = False) -> Ticket:
        """Validate and enqueue one semantic command. Raises CommandRejected,
        QueueFullError, EStopActive. preempt=True drops everything ahead."""
        if self._estop.is_set():
            raise EStopActive(f"emergency stop active: {self._estop_reason}")
        cmd = raw if isinstance(raw, BaseModel) else parse_command(raw)
        if cmd.action == "turn" and cmd.angle is not None and self.telemetry is None:
            raise CommandRejected(
                "turn by angle needs IMU telemetry (no source configured); "
                "use duration instead")
        self._ticket_counter += 1
        ticket = Ticket(self._ticket_counter, cmd.action)
        entry = _Entry(cmd, preempt)
        try:
            if preempt:
                self._drain_queue("preempt")
                self._queue.put_nowait(entry)
            else:
                self._queue.put_nowait(entry)
        except queue.Full as exc:
            raise QueueFullError("command queue full") from exc
        self.logger.log({"event": "submit", "ticket": ticket.id,
                         "action": ticket.action, "preempt": preempt})
        return ticket

    def update_target_bearing(self, bearing_deg: float | None) -> None:
        """Vision feed for an active approach behavior (None = not seen)."""
        if isinstance(self._current, _Approach):
            self._current.on_bearing(bearing_deg)

    def emergency_stop(self, reason: str) -> None:
        """Latch the estop. The next tick stops motion and drops everything;
        submits are refused until clear_estop()."""
        self._estop.set()
        self._estop_reason = reason
        self.logger.log({"event": "estop", "reason": reason})
        log.warning("emergency stop: %s", reason)

    def clear_estop(self) -> None:
        self._estop.clear()
        self._estop_reason = None
        self._stop_sent_since_estop = False
        self.logger.log({"event": "estop_cleared"})

    @property
    def estop_active(self) -> bool:
        return self._estop.is_set()

    @property
    def busy(self) -> bool:
        return self._current is not None or not self._queue.empty()

    # ------------------------------------------------------------- the loop

    def tick(self) -> None:
        """One control iteration. Safe to call manually for tests/sims."""
        now = self._clock()
        self._last_state = self.telemetry.poll() if self.telemetry else RobotState()

        if self._estop.is_set():
            self._handle_estop()
            return

        # Dequeue before stepping: a queued command always wins over the
        # running behavior — interrupt (stop first), then start the new one.
        try:
            entry = self._queue.get_nowait()
        except queue.Empty:
            entry = None
        if entry is not None:
            if self._current is not None:
                self._current.interrupt(self._behavior_ctx)
                self._current = None
            try:
                yaw0 = self._last_state.yaw_deg if self.telemetry else None
                behavior = _behavior_for(entry.cmd, yaw0)
            except CommandRejected as exc:
                self.logger.log({"event": "rejected", "reason": exc.reason})
            else:
                self._current = behavior
                self._current_started = True
                behavior.start(self._behavior_ctx)
                self.logger.log({"event": "start", "behavior": behavior.name})

        if self._current is not None:
            if self._feedback_is_stale(now):
                self.emergency_stop("telemetry feedback stale")
                self._handle_estop()
                return
            if self._current.step(self._behavior_ctx, now):
                self.logger.log({"event": "done", "behavior": self._current.name})
                self._current = None
                self._current_started = False
                self._tstate, restore = reset_cadence(self._tstate)
                if restore is not None:
                    self._send(restore)

    def _handle_estop(self) -> None:
        """Estop consequences: stop once, drop everything. Idempotent; runs
        inside the tick so all writes stay on the motion thread."""
        if not self._stop_sent_since_estop:
            self._send(dict(STOP_CMD))
            self._stop_sent_since_estop = True
        self._current = None
        self._current_started = False
        self._drain_queue("estop")

    def _feedback_is_stale(self, now: float) -> bool:
        if self.telemetry is None or self._current is None:
            return False
        ts = self._last_state.last_feedback_monotonic
        if ts is None:
            return False
        return now - ts > self.feedback_timeout_s

    def _send(self, native: dict) -> None:
        self.transport.send(native)
        self.logger.log({"event": "send", "native": native})

    def _drain_queue(self, why: str) -> None:
        while True:
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                return
            self.logger.log({"event": "dropped", "why": why,
                             "action": dropped.cmd.action})

    # ------------------------------------------------------------ threading

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.transport.open()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="wavego-motion")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.transport.close()

    def _run(self) -> None:
        period = 1.0 / max(self.rate_hz, 1.0)
        next_tick = self._clock()
        while self._running:
            self.tick()
            next_tick += period
            delay = next_tick - self._clock()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = self._clock()  # fell behind; don't accumulate debt
