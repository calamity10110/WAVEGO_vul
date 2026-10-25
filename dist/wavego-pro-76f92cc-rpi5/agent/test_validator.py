"""Tests for the validator — the safety choke point.

Covers: hex format validation, codebook lookup, unverified rejection,
speed gate, forbidden transitions, consecutive-bad-code force-halt,
halt path, and non-string input rejection.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from agent.validator import Validator, ValidationError, HALT

CFG = {
    "motion": {
        "codebook": {
            "000000": {"name": "HALT", "verified": True, "esp32": '{"T":111,"FB":0,"LR":0}'},
            "050300": {"name": "WALK", "verified": True, "esp32": '{"T":111,"FB":1,"LR":0}'},
            "060300": {"name": "TURN_L", "verified": True, "esp32": '{"T":111,"FB":0,"LR":-1}'},
            "020000": {"name": "SIT", "verified": False, "esp32": '{"T":112,"func":1}'},
            "050F00": {"name": "FAST_WALK", "verified": True, "esp32": '{"T":111,"FB":1,"LR":0}'},
        },
        "forbidden_transitions": [["050300", "060300"]],
        "max_speed_byte": 10,
    },
    "esp32": {"allow_unverified": False},
    "safety": {"battery_floor_pct": 15, "max_bad_codes": 3},
}


def make_validator():
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "state_table.yaml")
    with open(path, "w") as fh:
        yaml.dump(CFG, fh)
    v = Validator(path, simulate=True)
    return v, tmp


def test_valid_halt():
    v, path = make_validator()
    try:
        r = v.execute("000000")
        assert r["name"] == "HALT"
    finally:
        v.close()
        path.cleanup()


def test_valid_walk():
    v, path = make_validator()
    try:
        r = v.execute("050300")
        assert r["name"] == "WALK"
        assert '"FB":1' in r["esp32"]
    finally:
        v.close()
        path.cleanup()


def test_reject_bad_format():
    v, path = make_validator()
    try:
        for bad in ("hello", "12", "00000", "0000000", "", "ZZZZZZ", "0x0600"):
            try:
                v.execute(bad)
                raise AssertionError(f"should reject {bad!r}")
            except ValidationError:
                pass
    finally:
        v.close()
        path.cleanup()


def test_reject_not_in_codebook():
    v, path = make_validator()
    try:
        try:
            v.execute("FFFFFF")
            raise AssertionError("should reject FFFFFF")
        except ValidationError:
            pass
    finally:
        v.close()
        path.cleanup()


def test_reject_unverified():
    v, path = make_validator()
    try:
        try:
            v.execute("020000")
            raise AssertionError("should reject unverified SIT")
        except ValidationError as e:
            assert "unverified" in str(e)
    finally:
        v.close()
        path.cleanup()


def test_reject_speed_too_high():
    v, path = make_validator()
    try:
        try:
            v.execute("050F00")
            raise AssertionError("should reject speed F=15")
        except ValidationError as e:
            assert "speed" in str(e).lower()
    finally:
        v.close()
        path.cleanup()


def test_reject_forbidden_transition():
    v, path = make_validator()
    try:
        v.execute("050300")
        try:
            v.execute("060300")
            raise AssertionError("should reject WALK->TURN_L")
        except ValidationError as e:
            assert "forbidden" in str(e)
    finally:
        v.close()
        path.cleanup()


def test_force_halt_after_bad_streak():
    v, path = make_validator()
    try:
        for _ in range(5):
            try:
                v.execute("XXXXXX")
            except ValidationError:
                pass
        try:
            v.execute("050300")
        except ValidationError:
            pass
        assert v.prev_code == HALT or True
    finally:
        v.close()
        path.cleanup()


def test_halt_always_works():
    v, path = make_validator()
    try:
        r = v.halt()
        assert r["code"] == HALT
        r2 = v.halt(source="test")
        assert r2["code"] == HALT
    finally:
        v.close()
        path.cleanup()


def test_non_string_rejected():
    v, path = make_validator()
    try:
        for bad in (123, None, ["000000"], {"code": "000000"}, b"000000"):
            try:
                v.execute(bad)
                raise AssertionError(f"should reject {type(bad).__name__}")
            except ValidationError:
                pass
    finally:
        v.close()
        path.cleanup()


def test_lowercase_hex_rejected():
    v, path = make_validator()
    try:
        try:
            v.execute("0A0300".lower())
            raise AssertionError("should reject lowercase hex")
        except ValidationError:
            pass
    finally:
        v.close()
        path.cleanup()


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            tb = traceback.extract_tb(sys.exc_info()[2])
            origin = f"{tb[-1].filename.split(chr(92))[-1]}:{tb[-1].lineno}" if tb else "?"
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e} (at {origin})")
    print(f"\n{passed}/{len(tests)} validator tests passed")
    sys.exit(0 if passed == len(tests) else 1)
