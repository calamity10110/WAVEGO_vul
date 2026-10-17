"""Supervisor tests — the full lifecycle, deterministically.

A fake clock drives tick() by hand: no threads, no sleeps. The wire under
test is a MockTransport, so every assertion is on the exact native JSON the
supervisor emitted, in order.
"""

import pytest

from wavego_agent.schema import CommandRejected
from wavego_agent.supervisor import (
    EStopActive,
    MotionSupervisor,
    QueueFullError,
)
from wavego_agent.telemetry import MockTelemetry, RobotState, StaticYawSource
from wavego_agent.transport import MockTransport


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_sup(clock=None, telemetry=None, **kw):
    clock = clock or FakeClock()
    transport = MockTransport()
    sup = MotionSupervisor(transport, telemetry=telemetry, clock=clock, **kw)
    transport.open()
    return sup, transport, clock


def ticks(sup, clock, n, dt=0.1):
    for _ in range(n):
        clock.advance(dt)
        sup.tick()


# ------------------------------------------------------------------ basics

def test_stand_is_a_single_wire_line():
    sup, tr, clock = make_sup()
    sup.submit({"action": "stand"})
    sup.tick()
    assert tr.wire_lines == ['{"T":110}\n']


def test_stop_command_sends_the_stop_shape():
    sup, tr, clock = make_sup()
    sup.submit({"action": "stop"})
    sup.tick()
    assert tr.sent == [{"T": 111, "FB": 0, "LR": 0}]


def test_walk_starts_gait_then_stops_after_duration():
    sup, tr, clock = make_sup()
    sup.submit({"action": "walk", "direction": "forward", "speed": 0.5,
                "duration": 3})
    sup.tick()                                   # start: gait on
    assert tr.wire_lines[-1] == '{"T":111,"FB":1,"LR":0}\n'
    ticks(sup, clock, 10)                        # 1.0 s in: still walking
    assert tr.wire_lines[-1] == '{"T":111,"FB":1,"LR":0}\n'
    clock.advance(2.5)
    sup.tick()                                   # 3.5 s: stop
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'
    assert sup.busy is False


def test_commands_run_in_fifo_order():
    sup, tr, clock = make_sup()
    sup.submit({"action": "stand"})
    sup.submit({"action": "jump"})
    sup.tick()
    sup.tick()
    assert tr.wire_lines == ['{"T":110}\n', '{"T":112,"func":3}\n']


# -------------------------------------------------------------- preemption

def test_new_command_interrupts_running_walk():
    sup, tr, clock = make_sup()
    sup.submit({"action": "walk", "direction": "forward", "duration": 10})
    sup.tick()                                    # walking
    sup.submit({"action": "jump"})
    sup.tick()                                    # stop walk, jump
    assert tr.wire_lines[-2] == '{"T":111,"FB":0,"LR":0}\n'  # interrupt stop
    assert tr.wire_lines[-1] == '{"T":112,"func":3}\n'


def test_preempt_drops_queued_commands():
    sup, tr, clock = make_sup()
    sup.submit({"action": "stand"})
    sup.submit({"action": "jump"})
    sup.submit({"action": "sit"}, preempt=True)   # stand+jump dropped
    sup.tick()
    assert tr.wire_lines == ['{"T":112,"func":1}\n']


# ------------------------------------------------------------ emergency stop

def test_estop_sends_stop_and_refuses_new_work():
    sup, tr, clock = make_sup()
    sup.submit({"action": "walk", "direction": "forward", "duration": 10})
    sup.tick()
    sup.emergency_stop("operator")
    with pytest.raises(EStopActive):
        sup.submit({"action": "stand"})
    sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'
    ticks(sup, clock, 5)                          # stays put
    assert tr.wire_lines.count('{"T":111,"FB":0,"LR":0}\n') == 1  # sent once


def test_estop_drains_pending_commands():
    sup, tr, clock = make_sup()
    sup.submit({"action": "stand"})
    sup.submit({"action": "jump"})
    sup.emergency_stop("operator")
    ticks(sup, clock, 3)
    assert tr.wire_lines == ['{"T":111,"FB":0,"LR":0}\n']


def test_clear_estop_rearms_submission():
    sup, tr, clock = make_sup()
    sup.emergency_stop("test")
    sup.tick()
    sup.clear_estop()
    sup.submit({"action": "stand"})
    sup.tick()
    assert tr.wire_lines == ['{"T":111,"FB":0,"LR":0}\n', '{"T":110}\n']


# -------------------------------------------------------------- queue bound

def test_queue_full_rejects_instead_of_blocking():
    sup, tr, clock = make_sup(queue_size=1)
    sup.submit({"action": "stand"})
    with pytest.raises(QueueFullError):
        sup.submit({"action": "jump"})


# ------------------------------------------------------------ telemetry watchdog

def test_stale_feedback_estops_motion():
    state = RobotState(yaw_deg=0.0, last_feedback_monotonic=100.0)
    telemetry = MockTelemetry(state)
    sup, tr, clock = make_sup(telemetry=telemetry, feedback_timeout_s=2.0)
    sup.submit({"action": "walk", "direction": "forward", "duration": 10})
    sup.tick()                                    # fresh enough (t=100)
    clock.advance(0.5)
    state.last_feedback_monotonic = 100.5
    sup.tick()                                    # still fresh
    clock.advance(3.0)                            # feedback goes stale
    sup.tick()
    assert sup.estop_active
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'


def test_no_telemetry_means_no_watchdog():
    sup, tr, clock = make_sup()
    sup.submit({"action": "walk", "direction": "forward", "duration": 10})
    sup.tick()
    ticks(sup, clock, 20)
    assert not sup.estop_active


# ------------------------------------------------------------- yaw-based turn

def test_turn_by_angle_closes_loop_on_yaw():
    telemetry = StaticYawSource(0.0)
    sup, tr, clock = make_sup(telemetry=telemetry)
    sup.submit({"action": "turn", "direction": "left", "angle": 90})
    sup.tick()                                    # gait (0,-1) on
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":-1}\n'
    for _ in range(3):
        telemetry.rotate(30)                      # IMU reports progress
        clock.advance(0.1)
        sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'   # stopped
    assert telemetry.yaw_deg == pytest.approx(90)


def test_turn_by_angle_without_telemetry_is_rejected():
    sup, tr, clock = make_sup()
    with pytest.raises(CommandRejected):
        sup.submit({"action": "turn", "direction": "left", "angle": 90})


def test_turn_by_duration_runs_without_telemetry():
    sup, tr, clock = make_sup()
    sup.submit({"action": "turn", "direction": "right", "duration": 1})
    sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":1}\n'
    clock.advance(1.2)
    sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'


# ---------------------------------------------------------------- approach

def test_approach_aims_then_walks_then_stops_on_loss():
    sup, tr, clock = make_sup()
    sup.submit({"action": "approach", "target": "redball"})
    sup.tick()                                    # started, nothing sent yet
    assert tr.sent == []

    sup.update_target_bearing(-40.0)
    sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":1}\n'   # aim right (target at -40°)

    sup.update_target_bearing(0.0)
    sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":1,"LR":0}\n'   # walk

    for _ in range(5):                            # target vanishes
        sup.update_target_bearing(None)
        clock.advance(0.1)
        sup.tick()
    assert tr.wire_lines[-1] == '{"T":111,"FB":0,"LR":0}\n'   # stop
    assert sup.busy is False


def test_approach_only_writes_when_gait_changes():
    sup, tr, clock = make_sup()
    sup.submit({"action": "approach", "target": "redball"})
    sup.tick()
    sup.update_target_bearing(-40.0)
    sup.tick()
    n = len(tr.wire_lines)
    sup.update_target_bearing(-35.0)              # still on the right turn
    sup.tick()
    assert len(tr.wire_lines) == n


# -------------------------------------------------------- invalid at submit

def test_invalid_command_rejected_at_submit():
    sup, tr, clock = make_sup()
    with pytest.raises(CommandRejected):
        sup.submit({"action": "walk", "direction": "forward", "duration": 1,
                    "T": 999})
    sup.tick()
    assert tr.sent == []
