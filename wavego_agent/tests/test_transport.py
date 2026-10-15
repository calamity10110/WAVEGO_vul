"""Transport tests — the mock records exactly what would hit the wire."""

from wavego_agent.transport import MockTransport, encode_line


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
