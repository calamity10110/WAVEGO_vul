"""Tests for base_ctrl — command payload building, UART line buffering,
motion-heartbeat gating, and the no-hardware stand-ins.

Nothing here opens a real serial port: glob and serial.Serial are patched,
so ReadLine/BaseController are exercised through fakes. The module-level
config.yaml load runs for real (the file ships with the repo).

Run:  python -m pytest tests/test_base_ctrl.py
  or: python tests/test_base_ctrl.py   (plain-assert fallback, no pytest)
"""

import json
import os
import sys
import types
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base_ctrl
from base_ctrl import (
    BaseController,
    NullBaseController,
    ReadLine,
    ReadLineStub,
    is_motion_command,
)


class FakeSerial:
    """Minimal serial.Serial stand-in: queued reads, recorded writes."""

    def __init__(self, chunks=()):
        self._chunks = [c if isinstance(c, bytes) else c.encode("utf-8")
                        for c in chunks]
        self.written = []

    @property
    def in_waiting(self):
        return sum(len(c) for c in self._chunks)

    def read(self, n):
        n = max(1, min(n, self.in_waiting))
        head, self._chunks[0] = self._chunks[0][:n], self._chunks[0][n:]
        if not self._chunks[0]:
            self._chunks.pop(0)
        return head

    def write(self, data):
        self.written.append(data)
        return len(data)

    def reset_input_buffer(self):
        self._chunks = []

    def close(self):
        pass


def no_devices():
    """Keep ReadLine away from real /dev/ttyUSB* and /dev/ttyACM* devices."""
    return unittest.mock.patch("glob.glob", lambda pattern: [])


class _CaptureController(BaseController):
    """Skips BaseController.__init__ — records send_command payloads."""

    def __init__(self):
        self.commands = []

    def send_command(self, data):
        self.commands.append(data)


# ---------------------------------------------------------------- payloads

def test_speed_ctrl_payload():
    c = _CaptureController()
    c.base_speed_ctrl(0.5, -0.3)
    assert c.commands == [{"T": 1, "L": 0.5, "R": -0.3}]


def test_gimbal_ctrl_payload():
    c = _CaptureController()
    c.gimbal_ctrl(10, -5, 20, 3)
    assert c.commands == [{"T": 133, "X": 10, "Y": -5, "SPD": 20, "ACC": 3}]


def test_oled_payload_is_one_indexed():
    c = _CaptureController()
    c.base_oled(0, "hi")
    assert c.commands == [{"T": 202, "line": 1, "text": "hi", "update": 1}]


def test_rgb_light_payload():
    c = _CaptureController()
    c.rgb_light(2, 9, 0, 0)
    assert c.commands == [{"T": 201, "set": [2, 9, 0, 0]}]


def test_json_ctrl_passthrough():
    c = _CaptureController()
    c.base_json_ctrl({"T": 301, "name": "m"})
    assert c.commands == [{"T": 301, "name": "m"}]


def test_gait_engine_ctrl_payload_full():
    c = _CaptureController()
    c.gait_engine_ctrl(0, 0.4, heading=30, height=90, stride=20, distance=500)
    assert c.commands == [{"T": 117, "mode": 0, "speed": 0.4,
                           "heading": 30.0, "height": 90.0,
                           "stride": 20.0, "distance": 500.0}]


def test_gait_engine_ctrl_partial_update_omits_absent_fields():
    c = _CaptureController()
    c.gait_engine_ctrl(2, 0.6)
    assert c.commands == [{"T": 117, "mode": 2, "speed": 0.6}]


# -------------------------------------------------------- motion gating

def test_motion_commands():
    assert is_motion_command({"T": 1, "L": 0.5, "R": 0})
    assert is_motion_command({"T": 111, "FB": 1, "LR": 0})
    assert is_motion_command({"T": 111, "FB": 0, "LR": -1})
    assert is_motion_command({"T": 117, "mode": 0, "speed": 0.3})
    assert is_motion_command({"T": 117, "mode": 2, "speed": 0.8, "distance": 500})


def test_non_motion_commands():
    # a pure stop must not feed the failsafe heartbeat
    assert not is_motion_command({"T": 111, "FB": 0, "LR": 0})
    assert not is_motion_command({"T": 1, "L": 0, "R": 0})
    assert not is_motion_command({"T": 133, "X": 1})
    # any T:117 counts as motion: the firmware merges partial updates onto
    # its previous params, so even a height-only command can restart
    # walking at an inherited speed; over-reporting is the safe direction
    assert is_motion_command({"T": 117, "mode": 0, "speed": 0})
    assert is_motion_command({"T": 117, "mode": 1})
    assert not is_motion_command({})
    assert not is_motion_command("T:1")
    assert not is_motion_command(None)


# ------------------------------------------------------- ReadLine buffering

def test_readline_single_chunk():
    with no_devices():
        rl = ReadLine(FakeSerial(['{"T":1003}\n']))
    assert rl.readline() == b'{"T":1003}\n'


def test_readline_split_reads():
    with no_devices():
        rl = ReadLine(FakeSerial(['{"T":10', '03}\n']))
    assert rl.readline() == b'{"T":1003}\n'


def test_readline_keeps_leftover_in_buf():
    with no_devices():
        rl = ReadLine(FakeSerial(['a\nb\n']))
    assert rl.readline() == b'a\n'
    assert rl.readline() == b'b\n'


def test_readline_prefers_complete_line_already_buffered():
    with no_devices():
        rl = ReadLine(FakeSerial())  # nothing on the wire
    rl.buf = bytearray(b'{"T":1}\nrest')
    assert rl.readline() == b'{"T":1}\n'
    assert rl.buf == bytearray(b'rest')


# --------------------------------------------------------- feedback parsing

class FakeReadLine:
    def __init__(self, lines, in_waiting=0):
        self.lines = list(lines)
        self.s = types.SimpleNamespace(in_waiting=in_waiting)
        self.cleared = 0

    def readline(self):
        return self.lines.pop(0).encode("utf-8")

    def clear_buffer(self):
        self.cleared += 1


def _bare_controller(rl, connected=True):
    b = BaseController.__new__(BaseController)
    b.connected = connected
    b.rl = rl
    b.data_buffer = None
    b.base_data = None
    b.feedback_hook = None
    return b


def test_feedback_returns_parsed_json():
    b = _bare_controller(FakeReadLine(['{"T":1003,"v":7.4}\n']))
    assert b.feedback_data() == {"T": 1003, "v": 7.4}
    assert b.base_data == {"T": 1003, "v": 7.4}


def test_feedback_drains_waiting_lines_until_telemetry():
    rl = FakeReadLine(['{"T":133}\n', '{"T":1003}\n'], in_waiting=9)
    b = _bare_controller(rl)
    assert b.feedback_data() == {"T": 1003}


def test_feedback_disconnected_returns_none_without_reading():
    rl = FakeReadLine(['{"T":1003}\n'])
    b = _bare_controller(rl, connected=False)
    assert b.feedback_data() is None
    assert rl.lines  # nothing consumed


def test_feedback_malformed_json_is_swallowed():
    b = _bare_controller(FakeReadLine(['not json']))
    assert b.feedback_data() is None


def test_feedback_hook_fires_for_every_parsed_line():
    rl = FakeReadLine(['{"T":-117,"done":true,"traveled":120.0,"cycles":2}\n',
                       '{"T":1001,"v":7.4}\n'], in_waiting=9)
    b = _bare_controller(rl)
    seen = []
    b.feedback_hook = seen.append
    b.feedback_data()
    assert [d["T"] for d in seen] == [-117, 1001]


def test_feedback_hook_absent_or_broken_never_breaks_parsing():
    rl = FakeReadLine(['{"T":1003,"v":7.4}\n'])
    b = _bare_controller(rl)          # no hook set
    assert b.feedback_data() == {"T": 1003, "v": 7.4}

    def bad_hook(d):
        raise RuntimeError("hook blew up")
    rl2 = FakeReadLine(['{"T":1003}\n'])
    b2 = _bare_controller(rl2)
    b2.feedback_hook = bad_hook
    assert b2.feedback_data() == {"T": 1003}


def test_feedback_hook_ignores_lines_without_t():
    rl = FakeReadLine(['{"no_t":1}\n'], in_waiting=1)
    b = _bare_controller(rl)
    seen = []
    b.feedback_hook = seen.append
    b.feedback_data()
    assert seen == []


# ------------------------------------------------- command pipeline (UART)

def serial_patch(opens, instances, fail_after=None):
    """Patch base_ctrl.serial.Serial; records every open. fail_after=N makes
    open N+1 raise (device gone)."""

    def fake_serial(dev, baud, timeout=1):
        if fail_after is not None and len(opens) >= fail_after:
            raise OSError("no device")
        opens.append((dev, baud))
        ser = FakeSerial()
        instances.append(ser)
        return ser

    return unittest.mock.patch.object(base_ctrl.serial, "Serial", fake_serial)


def _live_controller(opens, instances):
    with no_devices(), serial_patch(opens, instances):
        b = BaseController("/dev/fake", 115200)
    assert b.connected is True
    assert b._uart == "/dev/fake"
    assert b._baud == 115200
    return b


def test_controller_init_wiring():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    assert opens == [("/dev/fake", 115200)]


def test_motion_command_writes_json_line_and_fires_hook_once():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    calls = []
    b.motion_hook = lambda t: calls.append(t)
    b._process_one({"T": 1, "L": 0.5, "R": -0.3})
    assert instances[0].written == [
        (json.dumps({"T": 1, "L": 0.5, "R": -0.3}) + "\n").encode("utf-8")
    ]
    assert len(calls) == 1


def test_stop_and_non_motion_commands_do_not_fire_hook():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    calls = []
    b.motion_hook = lambda t: calls.append(t)
    b._process_one({"T": 111, "FB": 0, "LR": 0})
    b._process_one({"T": 133, "X": 1, "Y": 2, "SPD": 3, "ACC": 0})
    assert len(instances[0].written) == 2
    assert calls == []


def test_hook_exceptions_never_break_the_pipeline():
    opens, instances = [], []
    b = _live_controller(opens, instances)

    def bad_hook(t):
        raise RuntimeError("hook blew up")

    b.motion_hook = bad_hook
    b._process_one({"T": 1, "L": 1, "R": 1})
    assert len(instances[0].written) == 1


def test_disconnected_reconnect_fails_keeps_heartbeat_without_write():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    b.connected = False
    with no_devices(), serial_patch(opens, instances, fail_after=1):
        calls = []
        b.motion_hook = lambda t: calls.append(t)
        b._process_one({"T": 111, "FB": 1, "LR": 0})
    assert b.connected is False
    assert len(calls) == 1            # heartbeat still tracked while down
    assert instances[0].written == []  # nothing written on a dead link


def test_disconnected_reconnect_success_does_not_replay_command():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    b.connected = False
    with no_devices(), serial_patch(opens, instances):
        b._process_one({"T": 1, "L": 1, "R": 1})
    assert b.connected is True
    assert len(opens) == 2             # initial open + reconnect
    assert instances[0].written == []  # command dropped, not replayed


def test_reconnect_backoff_within_five_second_window():
    opens, instances = [], []
    b = _live_controller(opens, instances)
    clock = [1000.0]
    with no_devices(), serial_patch(opens, instances):
        with unittest.mock.patch.object(base_ctrl.time, "time", lambda: clock[0]):
            b.connected = False
            b._process_one({"T": 1, "L": 1, "R": 1})   # attempt at t=1000
            b.connected = False
            clock[0] = 1002.0
            b._process_one({"T": 1, "L": 1, "R": 1})   # inside window: no reopen
            assert len(opens) == 2
            clock[0] = 1006.0
            b._process_one({"T": 1, "L": 1, "R": 1})   # window elapsed: reopen
            assert len(opens) == 3


# ----------------------------------------------------------- no-hw stand-ins

COMMAND_METHODS = ["send_command", "base_json_ctrl", "base_speed_ctrl",
                   "gait_engine_ctrl", "gimbal_ctrl", "base_oled",
                   "rgb_light", "feedback_data", "gimbal_dev_close"]


def test_null_base_controller_absorbs_everything():
    null = NullBaseController()
    null.send_command({"T": 1})
    null.base_json_ctrl({"T": 1})
    null.base_speed_ctrl(1, 1)
    null.gait_engine_ctrl(0, 0.3)
    null.gimbal_ctrl(0, 0, 10, 0)
    null.base_oled(0, "hi")
    null.rgb_light(0, 1, 2, 3)
    null.breath_light(1)
    null.gimbal_dev_close()
    assert null.feedback_data() is None
    assert null.connected is False


def test_app_facing_api_matches_between_real_and_null():
    for m in COMMAND_METHODS:
        assert callable(getattr(BaseController, m, None)), \
            f"BaseController lost {m}"
        assert callable(getattr(NullBaseController, m, None)), \
            f"NullBaseController lost {m}"


def test_readline_stub_is_inert():
    stub = ReadLineStub()
    assert stub.readline() == b""
    stub.buf = bytearray(b"junk")
    stub.clear_buffer()
    assert stub.buf == bytearray()
    stub.read_sensor_data()
    stub.lidar_data_recv()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} FAILURES'}")
    sys.exit(1 if failures else 0)
