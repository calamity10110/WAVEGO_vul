import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from failsafe_keepalive import STOP_CMD, decide, uart_device

NOW = 1000.0


def test_healthy_poll_resets():
    assert decide(NOW, None, 0) == 'reset'
    assert decide(NOW, NOW - 2, 0) == 'reset'


def test_failures_below_threshold_watch():
    assert decide(NOW, NOW - 2, 1) == 'watch'
    assert decide(NOW, NOW - 2, 2) == 'watch'


def test_dead_with_recent_motion_stops():
    assert decide(NOW, NOW - 2, 3) == 'stop'
    assert decide(NOW, NOW - 9, 4) == 'stop'


def test_dead_without_motion_watch():
    assert decide(NOW, None, 3) == 'watch'


def test_dead_with_stale_motion_watch():
    assert decide(NOW, NOW - 11, 3) == 'watch'
    assert decide(NOW, NOW - 600, 5) == 'watch'


def test_already_stopped_never_refires():
    assert decide(NOW, NOW - 2, 3, already_stopped=True) == 'watch'
    assert decide(NOW, NOW - 1, 6, already_stopped=True) == 'watch'


def test_window_boundary_strict():
    assert decide(NOW, NOW - 10, 3) == 'stop'
    assert decide(NOW, NOW - 10.1, 3) == 'watch'


def test_custom_thresholds():
    assert decide(NOW, NOW - 4, 5, dead_after=5, motion_window_s=5) == 'stop'
    assert decide(NOW, NOW - 4, 4, dead_after=5, motion_window_s=5) == 'watch'


def test_stop_cmd_is_a_firmware_stop():
    # The direct-write payload must be a T:111 stop under the gait contract,
    # matching base_ctrl.is_motion_command's zero-component no-motion shape.
    assert STOP_CMD == {"T": 111, "FB": 0, "LR": 0}


def test_uart_device_selection(monkeypatch):
    import failsafe_keepalive as fk
    monkeypatch.setattr(fk, 'is_pi5', lambda: True)
    assert uart_device() == '/dev/ttyAMA0'
    monkeypatch.setattr(fk, 'is_pi5', lambda: False)
    assert uart_device() == '/dev/serial0'


def test_is_pi5_reads_cpuinfo(tmp_path, monkeypatch):
    import failsafe_keepalive as fk
    fake = tmp_path / 'cpuinfo'
    fake.write_text('Model\t\t: Raspberry Pi 5 Model B Rev 1.0\n')
    monkeypatch.setattr('builtins.open', lambda p, *a, **k: fake.open('r'))
    assert fk.is_pi5() is True
    fake.write_text('Model\t\t: Raspberry Pi 4 Model B Rev 1.4\n')
    assert fk.is_pi5() is False

