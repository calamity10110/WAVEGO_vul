import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vlm_ctrl.capture import grab_frame
from vlm_ctrl.dispatch import Dispatcher

JPEG_A = b"\xff\xd8" + b"\x01\x02bodyA" + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"\x03\x04bodyB-longer" + b"\xff\xd9"


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_content(self, n):
        return iter(self._chunks)

    def close(self):
        pass


def test_grab_frame_extracts_first_jpeg(tmp_path):
    out = str(tmp_path / "f.jpg")
    stream = FakeStream([b"frame\r\n" + JPEG_A[:6], JPEG_A[6:] + b"trailing junk"])
    info = grab_frame("http://x/video_feed", out, getter=lambda u, t: stream)
    assert info["bytes"] == len(JPEG_A)
    with open(out, "rb") as fh:
        assert fh.read() == JPEG_A


def test_grab_frame_hash_tracks_content(tmp_path):
    out = str(tmp_path / "f.jpg")
    i1 = grab_frame("u", out, getter=lambda u, t: FakeStream([JPEG_A]))
    i2 = grab_frame("u", out, getter=lambda u, t: FakeStream([JPEG_B]))
    assert i1["hash"] != i2["hash"]


def test_grab_frame_raises_on_garbage(tmp_path):
    out = str(tmp_path / "f.jpg")
    try:
        grab_frame("u", out, getter=lambda u, t: FakeStream([b"no markers here" * 1000]))
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")


def test_dispatch_sends_and_arms_timeout():
    sent = []
    clock = {"t": 0.0}
    d = Dispatcher("http://robot", move_timeout_s=5.0,
                   poster=lambda url, p: sent.append((url, p)) or True,
                   clock=lambda: clock["t"])
    assert d.send({"T": 111, "FB": 1, "LR": 0}) is True
    assert sent[-1][0] == "http://robot/api/cmd"
    assert d.tick() is False                       # not yet expired
    clock["t"] += 6.0
    assert d.tick() is True                        # fired
    assert sent[-1][1] == {"T": 111, "FB": 0, "LR": 0}


def test_dispatch_stop_disarms_timeout():
    sent = []
    clock = {"t": 0.0}
    d = Dispatcher("http://robot", poster=lambda url, p: sent.append(p) or True,
                   clock=lambda: clock["t"])
    d.send({"T": 111, "FB": 1, "LR": 0})
    d.send({"T": 111, "FB": 0, "LR": 0})           # stop disarms
    clock["t"] += 99
    assert d.tick() is False
    assert sent[-1] == {"T": 111, "FB": 0, "LR": 0}


def test_dispatch_none_is_noop():
    sent = []
    d = Dispatcher("http://robot", poster=lambda url, p: sent.append(p) or True,
                   clock=lambda: 0.0)
    assert d.send(None) is True
    assert sent == []


def test_dispatch_non_movement_does_not_arm():
    sent = []
    clock = {"t": 0.0}
    d = Dispatcher("http://robot", poster=lambda url, p: sent.append(p) or True,
                   clock=lambda: clock["t"])
    d.send({"T": 110})
    clock["t"] += 99
    assert d.tick() is False
