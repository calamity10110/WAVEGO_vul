"""Heartbeat tests for AgentLoop — the failure ladder and lifecycle.

Covers: 5-strike exit, counter reset on success, learner observe/idle
wiring, halt-on-shutdown, --once single cycle, KeyboardInterrupt path,
and the ESTOP early-bypass through the real run_once pipeline.
"""

import os
import sys
import tempfile
from unittest.mock import patch

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.loop import AgentLoop, MAX_CONSECUTIVE_FAILURES
from agent.validator import Validator

CFG = {
    "motion": {
        "codebook": {
            "000000": {"name": "HALT", "verified": True,
                       "esp32": '{"T":111,"FB":0,"LR":0}'},
            "050300": {"name": "WALK", "verified": True,
                       "esp32": '{"T":111,"FB":1,"LR":0}'},
        },
        "forbidden_transitions": [],
        "max_speed_byte": 10,
    },
    "esp32": {"allow_unverified": False},
    "safety": {"battery_floor_pct": 15, "max_bad_codes": 100},
}


class SpyValidator(Validator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.halt_sources = []

    def halt(self, source="safety"):
        self.halt_sources.append(source)
        return super().halt(source=source)


class FakeLearner:
    def __init__(self):
        self.observes = []
        self.idle_passes = 0

    def observe(self, key, value, source="model", outcome="ok"):
        self.observes.append((key, value, outcome))

    def idle_pass(self, force=False):
        self.idle_passes += 1

    def close(self):
        pass


def make_loop(once=False):
    tmp = tempfile.TemporaryDirectory()
    cfg_path = os.path.join(tmp.name, "state_table.yaml")
    with open(cfg_path, "w") as fh:
        yaml.dump(CFG, fh)
    validator = SpyValidator(cfg_path, simulate=True)
    learner = FakeLearner()
    loop = AgentLoop(config_path=cfg_path, simulate=True, once=once,
                     log_dir=tmp.name, validator=validator, learner=learner)
    return loop, validator, learner, tmp


def test_exit_after_max_failures():
    loop, validator, learner, tmp = make_loop()
    try:
        def boom():
            raise RuntimeError("cycle exploded")
        loop.run_once = boom
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert loop.consecutive_failures == MAX_CONSECUTIVE_FAILURES
        assert "loop_guard" in validator.halt_sources
        assert validator.halt_sources[-1] == "shutdown"
    finally:
        validator.close()
        tmp.cleanup()


def test_success_resets_counter():
    loop, validator, learner, tmp = make_loop()
    try:
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first cycle fails")
            loop.once = True
        loop.run_once = flaky
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert calls["n"] == 2
        assert loop.consecutive_failures == 0
        assert learner.observes == [
            ("cycle", "error: RuntimeError", "fail"),
            ("cycle", "ok", "ok"),
        ]
        assert learner.idle_passes == 1
    finally:
        validator.close()
        tmp.cleanup()


def test_learner_wiring():
    loop, validator, learner, tmp = make_loop(once=True)
    try:
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert learner.observes == [("cycle", "ok", "ok")]
        assert learner.idle_passes == 1
    finally:
        validator.close()
        tmp.cleanup()


def test_halt_on_shutdown():
    loop, validator, learner, tmp = make_loop(once=True)
    try:
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert validator.halt_sources[-1] == "shutdown"
    finally:
        validator.close()
        tmp.cleanup()


def test_once_single_cycle():
    loop, validator, learner, tmp = make_loop(once=True)
    try:
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert loop.cycle_count == 1
        assert loop.consecutive_failures == 0
    finally:
        validator.close()
        tmp.cleanup()


def test_keyboard_interrupt_shutdown():
    loop, validator, learner, tmp = make_loop()
    try:
        def interrupted():
            raise KeyboardInterrupt
        loop.run_once = interrupted
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert "shutdown" in validator.halt_sources
        assert loop.consecutive_failures == 0
    finally:
        validator.close()
        tmp.cleanup()


def test_estop_bypass():
    loop, validator, learner, tmp = make_loop(once=True)
    try:
        loop.ingest.get_user_input = lambda: {"intent": "__ESTOP__"}
        loop.ingest.get_mission = lambda: None
        loop.ingest.get_frame = lambda: None
        with patch("agent.loop.time.sleep"):
            loop.run()
        assert "estop" in validator.halt_sources
        assert validator.prev_code == "000000"
    finally:
        validator.close()
        tmp.cleanup()


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            tb = traceback.extract_tb(sys.exc_info()[2])
            origin = (f"{tb[-1].filename.split(chr(92))[-1]}:{tb[-1].lineno}"
                      if tb else "?")
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e} (at {origin})")
    print(f"\n{passed}/{len(tests)} loop heartbeat tests passed")
    sys.exit(0 if passed == len(tests) else 1)
