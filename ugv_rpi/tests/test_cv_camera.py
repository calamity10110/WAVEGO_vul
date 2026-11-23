"""CameraRack extraction + cv_ctrl refactor tests.

The usb -> csi -> oak chain moved out of OpencvFuncs into cv_camera.py so
the degradation ladder (each backend failing into the next, all failing
into a placeholder frame) is testable without hardware. Network-free:
lsusb/Picamera2/depthai are absent or faked on the test box.
"""

import importlib
import importlib.util
import os
import sys
import types

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

cv_camera = importlib.import_module("cv_camera")


class FakeCam:
    def __init__(self, result=(True, "FRAME")):
        self.result = result
        self.released = False

    def read(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def release(self):
        self.released = True

    def set(self, key, value):
        pass


@pytest.fixture
def rack(monkeypatch):
    monkeypatch.setattr(cv_camera, "usb_camera_detection", lambda: False)
    monkeypatch.setattr(cv_camera.time, "sleep", lambda s: None)
    return cv_camera.CameraRack(640, 480)


def test_no_camera_chain_falls_through_to_placeholder(rack):
    assert rack.usb_camera_connected is False
    assert rack.csi_camera_connected is False
    assert rack.oak_camera_connected is False
    frame, fallback = rack.read(55)
    assert frame is None
    assert fallback[:2] == b"\xff\xd8"
    img = cv2.imdecode(np.frombuffer(fallback, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape == (480, 640, 3)


def test_usb_success_returns_frame(rack):
    rack.usb_camera_connected = True
    rack.camera = FakeCam((True, "FRAME"))
    frame, fallback = rack.read(55)
    assert frame == "FRAME"
    assert fallback is None


def test_usb_read_failure_reopens_device(rack, monkeypatch):
    rack.usb_camera_connected = True
    original = FakeCam((False, None))
    rack.camera = original
    reopened = FakeCam()
    opened = []
    monkeypatch.setattr(cv_camera.cv2, "VideoCapture",
                        lambda idx: opened.append(idx) or reopened)
    frame, fallback = rack.read(55)
    assert original.released is True
    assert opened == [0]
    assert rack.camera is reopened
    assert frame is None and fallback is None


def test_csi_capture(rack):
    rack.csi_camera_connected = True
    rack.picam2 = types.SimpleNamespace(capture_array=lambda: "CSI_FRAME")
    frame, fallback = rack.read(55)
    assert frame == "CSI_FRAME"
    assert fallback is None


def test_read_exception_returns_error_placeholder(rack, capsys):
    rack.usb_camera_connected = True
    rack.camera = FakeCam(RuntimeError("boom"))
    frame, fallback = rack.read(55)
    assert frame is None
    assert "boom" in capsys.readouterr().out
    img = cv2.imdecode(np.frombuffer(fallback, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape == (480, 640, 3)


def test_usb_detection_graceful_without_lsusb(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("lsusb")
    monkeypatch.setattr(cv_camera.subprocess, "check_output", missing)
    assert cv_camera.usb_camera_detection() is False


def test_usb_detection_matches_camera_listing(monkeypatch):
    monkeypatch.setattr(cv_camera.subprocess, "check_output",
                        lambda *a, **k: b"Bus 001 Device 005: ID 046d:0825 Logitech Camera")
    assert cv_camera.usb_camera_detection() is True
    monkeypatch.setattr(cv_camera.subprocess, "check_output",
                        lambda *a, **k: b"Bus 001 Device 002: ID 1d6b:0002 Root Hub")
    assert cv_camera.usb_camera_detection() is False


def _stub_cv2_for_pinned_api(monkeypatch):
    if not hasattr(cv2, "CascadeClassifier"):
        monkeypatch.setattr(cv2, "CascadeClassifier",
                            lambda *a, **k: types.SimpleNamespace(), raising=False)
    dnn = getattr(cv2, "dnn", None)
    if dnn is None:
        dnn = types.SimpleNamespace()
        monkeypatch.setattr(cv2, "dnn", dnn, raising=False)
    if not hasattr(dnn, "readNetFromCaffe"):
        monkeypatch.setattr(dnn, "readNetFromCaffe",
                            lambda *a, **k: types.SimpleNamespace(), raising=False)


def _load_cv_ctrl(monkeypatch):
    _stub_cv2_for_pinned_api(monkeypatch)
    for name in ("imutils", "imageio", "mediapipe"):
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            if name == "mediapipe":
                stub.solutions = types.SimpleNamespace(
                    drawing_utils=types.SimpleNamespace(),
                    hands=types.SimpleNamespace(
                        Hands=lambda **kw: types.SimpleNamespace()),
                    face_detection=types.SimpleNamespace(
                        FaceDetection=lambda **kw: types.SimpleNamespace()),
                    pose=types.SimpleNamespace(
                        Pose=lambda **kw: types.SimpleNamespace()),
                )
            monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location(
        "cv_ctrl_under_test", os.path.join(ROOT, "cv_ctrl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cv_ctrl_module_structure_after_extraction(monkeypatch):
    import inspect
    mod = _load_cv_ctrl(monkeypatch)
    assert hasattr(mod.OpencvFuncs, "frame_process")
    assert "CameraRack(" in inspect.getsource(mod.OpencvFuncs.__init__)
    assert mod.CameraRack is cv_camera.CameraRack
    assert not hasattr(mod.OpencvFuncs, "usb_camera_detection")
    assert not hasattr(mod.OpencvFuncs, "map_value")
    assert not hasattr(mod.OpencvFuncs, "calculate_angle")
    assert callable(mod.calculate_angle)
    assert callable(mod.format_json_numbers)


def test_cv_ctrl_instantiates_with_camera_chain(monkeypatch):
    monkeypatch.setattr(cv_camera, "usb_camera_detection", lambda: False)
    mod = _load_cv_ctrl(monkeypatch)
    inst = mod.OpencvFuncs(ROOT, base_ctrl=None)
    assert inst.cam.usb_camera_connected is False
    assert inst.cam.csi_camera_connected is False
    assert inst.cam.oak_camera_connected is False
    assert callable(inst.frame_process)


def test_calculate_angle_and_distance(monkeypatch):
    mod = _load_cv_ctrl(monkeypatch)
    P = types.SimpleNamespace
    right_angle = mod.calculate_angle(P(x=0, y=0), P(x=1, y=0),
                                      P(x=0, y=0), P(x=0, y=1))
    assert abs(right_angle - 90.0) < 1e-6
    dist = mod.calculate_distance(P(x=0, y=0), P(x=3, y=4))
    assert dist == 5.0


def test_format_json_numbers_rounds_floats(monkeypatch):
    mod = _load_cv_ctrl(monkeypatch)
    out = mod.format_json_numbers(
        {"v": 7.826543, "nested": [1.5555, 3], "raw": "x"})
    assert out == {"v": 7.83, "nested": [1.56, 3], "raw": "x"}
