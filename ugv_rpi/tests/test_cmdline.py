"""Dispatch tests for cmdline_ctrl — web-console command parity.

Proves: every dispatch key routes to its handler, JSON passthrough is
exact, the `v` alias works, and malformed input is a silent no-op (len
guards replace the old IndexError crashes).

Heavy Pi-only modules (aiortc, cv_ctrl, os_info) are stubbed only when
absent, so the suite runs on any dev box and skips cleanly if app.py
itself cannot import even with stubs.
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stub_if_missing(name, **attrs):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for key, val in attrs.items():
            setattr(mod, key, val)
        sys.modules[name] = mod


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass


class _FakeOpencvFuncs:
    def __init__(self, path, base):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeSystemInfo:
    def __getattr__(self, name):
        return lambda *a, **k: {}


_stub_if_missing("aiortc",
                 RTCPeerConnection=_Dummy,
                 RTCSessionDescription=_Dummy)
_stub_if_missing("cv_ctrl", OpencvFuncs=_FakeOpencvFuncs)
_stub_if_missing("os_info", SystemInfo=_FakeSystemInfo)

try:
    import app as app_mod
    IMPORT_ERROR = None
except Exception as e:
    app_mod = None
    IMPORT_ERROR = e


class FakeBase:
    def __init__(self):
        self.sent = []

    def base_json_ctrl(self, obj):
        self.sent.append(obj)


class FakeCvf:
    def __init__(self):
        self.calls = []

    def show_recv_info(self, on):
        self.calls.append(("show_recv", on))

    def change_target_color(self, lo, hi):
        self.calls.append(("color", lo, hi))

    def selet_target_color(self, sel):
        self.calls.append(("select", sel))

    def set_video_quality(self, q):
        self.calls.append(("quality", q))

    def set_pt_track_args(self, a, b):
        self.calls.append(("track", a, b))

    def timelapse(self, *args):
        self.calls.append(("timelapse",) + args)

    def mission_stop(self):
        self.calls.append(("mission_stop",))

    def update_base_data(self, d):
        self.calls.append(("update_base", d))


@unittest.skipIf(app_mod is None, f"app import unavailable: {IMPORT_ERROR}")
class CmdlineDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fake_base = FakeBase()
        self.fake_cvf = FakeCvf()
        self._orig_base = app_mod.base
        self._orig_cvf = app_mod.cvf
        app_mod.base = self.fake_base
        app_mod.cvf = self.fake_cvf

    def tearDown(self):
        app_mod.base = self._orig_base
        app_mod.cvf = self._orig_cvf

    def test_base_c_json_passthrough(self):
        app_mod.cmdline_ctrl('base -c {"T":111,"FB":1,"LR":0}')
        self.assertEqual(self.fake_base.sent, [{"T": 111, "FB": 1, "LR": 0}])

    def test_base_recv_off(self):
        app_mod.cmdline_ctrl("base -r off")
        self.assertEqual(self.fake_cvf.calls, [("show_recv", False)])

    def test_send_add_broadcast(self):
        app_mod.cmdline_ctrl("send -a -b")
        self.assertEqual(self.fake_base.sent,
                         [{"T": 303, "mac": "FF:FF:FF:FF:FF:FF"}])

    def test_send_group_message(self):
        app_mod.cmdline_ctrl("send -g hello world")
        self.assertEqual(
            self.fake_base.sent[0]["megs"], "hello world")
        self.assertEqual(self.fake_base.sent[0]["T"], 305)

    def test_cv_range_valid(self):
        app_mod.cmdline_ctrl("cv -r [0,30,60] [10,90,200]")
        self.assertEqual(self.fake_cvf.calls,
                         [("color", [0, 30, 60], [10, 90, 200])])

    def test_cv_range_out_of_range_noop(self):
        app_mod.cmdline_ctrl("cv -r [0,300,60] [10,90,200]")
        self.assertEqual(self.fake_cvf.calls, [])

    def test_cv_select(self):
        app_mod.cmdline_ctrl("cv -s red")
        self.assertEqual(self.fake_cvf.calls, [("select", "red")])

    def test_video_quality(self):
        app_mod.cmdline_ctrl("video -q 55")
        self.assertIn(("quality", 55), self.fake_cvf.calls)

    def test_v_alias(self):
        app_mod.cmdline_ctrl("v -q 40")
        self.assertIn(("quality", 40), self.fake_cvf.calls)

    def test_track(self):
        app_mod.cmdline_ctrl("track 0.5 0.5")
        self.assertIn(("track", "0.5", "0.5"), self.fake_cvf.calls)

    def test_timelapse_stop(self):
        app_mod.cmdline_ctrl("timelapse --stop")
        self.assertIn(("mission_stop",), self.fake_cvf.calls)

    def test_test_command(self):
        app_mod.cmdline_ctrl("test")
        self.assertEqual(self.fake_cvf.calls[0][0], "update_base")

    def test_unknown_command_noop(self):
        app_mod.cmdline_ctrl("bogus x y")
        self.assertEqual(self.fake_base.sent, [])
        self.assertEqual(self.fake_cvf.calls, [])

    def test_empty_input_noop(self):
        app_mod.cmdline_ctrl("")
        app_mod.cmdline_ctrl("   ")
        self.assertEqual(self.fake_base.sent, [])

    def test_missing_args_no_crash(self):
        for line in ("base", "base -c", "cv -r [1,2,3]", "video -q",
                     "send -a", "track 0.5", "timelapse -s"):
            app_mod.cmdline_ctrl(line)
        self.assertEqual(self.fake_base.sent, [])
        self.assertEqual(self.fake_cvf.calls, [])

    def test_bad_json_propagates(self):
        with self.assertRaises(ValueError):
            app_mod.cmdline_ctrl("base -c not-json")

    def test_dispatch_covers_all_keys(self):
        expected = {"base", "send", "cv", "video", "v", "track",
                    "timelapse", "s", "test"}
        self.assertEqual(set(app_mod._CMD_DISPATCH.keys()), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
