"""Transport tests — the mock records exactly what would hit the wire."""

from wavego_agent.transport import MockTransport, encode_line, resolve_serial_device


def test_encode_line_matches_firmware_framing():
    assert encode_line({"T": 111, "FB": 1, "LR": 0}) == b'{"T":111,"FB":1,"LR":0}\n'


def test_mock_records_dicts_and_wire():
    tr = MockTransport()
    tr.open()
    tr.send({"T": 110})
    tr.send({"T": 111, "FB": 1, "LR": 0})
    assert tr.sent == [{"T": 110}, {"T": 111, "FB": 1, "LR": 0}]
    assert tr.wire_lines == ['{"T":110}\n', '{"T":111,"FB":1,"LR":0}\n']
    tr.close()
    assert tr.opened is False


# ------------------------------------------------- ESP32 over USB or UART

def exists_in(*paths):
    return lambda p: p in paths


def test_explicit_device_always_wins():
    assert resolve_serial_device("/dev/ttyUSB1",
                                 exists=exists_in("/dev/ttyACM0")) \
        == "/dev/ttyUSB1"


def _usb_glob(pattern):
    return {"/dev/ttyACM*": ["/dev/ttyACM0"],
            "/dev/ttyUSB*": ["/dev/ttyUSB0"]}[pattern]


def test_auto_prefers_usb_acm_then_usb_serial():
    globfn = _usb_glob
    assert resolve_serial_device("auto",
                                 exists=exists_in("/dev/ttyACM0", "/dev/ttyUSB0"),
                                 globfn=globfn) == "/dev/ttyACM0"
    assert resolve_serial_device("auto", exists=exists_in("/dev/ttyUSB0"),
                                 globfn=globfn) == "/dev/ttyUSB0"


def test_auto_falls_back_to_gpio_uart():
    # no USB serial: Pi 5 -> ttyAMA0, otherwise serial0 (cpuinfo read fails
    # off-Pi and lands on serial0)
    dev = resolve_serial_device(None, exists=exists_in("/dev/serial0"),
                                globfn=lambda pattern: [])
    assert dev in ("/dev/ttyAMA0", "/dev/serial0")
