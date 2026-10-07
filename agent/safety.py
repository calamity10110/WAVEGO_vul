"""Safety gates — deterministic, non-AI checks that run before the model
and between the model and the validator.

Purpose:
    Implements the hardcode safety layer: frame sanity (frozen/loop
    detection), prompt-injection scan, verdict vocabulary enforcement,
    and the pre-motion checklist on live telemetry. These gates are
    pure functions — no LLM calls, no network, no I/O.

Dependencies:
    stdlib only (re, hashlib).

Interface:
    check_frame_sanity(frames) -> (ok, reason)
    scan_injection(text) -> (ok, reason)
    check_verdict(text, vocab) -> (ok, verdict)
    pre_motion_checklist(validator) -> (ok, details)
    encode_sensor_string(battery, roll, pitch, servo_flags) -> str

Expected outcome:
    Each gate returns (bool, str). A False from any gate blocks the
    pipeline — no model call, no motion.
"""

import hashlib
import re

VERDICTS = {"YES", "NO", "PARTIAL", "BLOCKED", "DRIFTED", "CLEAR"}

INJECTION_RX = re.compile(
    r"ignore previous|disregard|you are now|system:|###|```"
    r"|new instructions|override safety",
    re.IGNORECASE)


def check_frame_sanity(frames, frozen_threshold=0.98, loop_window=6):
    """Detect frozen frames (identical hashes) and A,B,A loops."""
    if len(frames) < 2:
        return True, "insufficient history"

    hashes = [hashlib.md5(f).hexdigest() if isinstance(f, bytes)
              else hashlib.md5(str(f).encode()).hexdigest() for f in frames]

    if len(hashes) >= 2 and hashes[-1] == hashes[-2]:
        identical = sum(1 for h in hashes if h == hashes[-1])
        if identical / len(hashes) > frozen_threshold:
            return False, f"frozen frame: {identical}/{len(hashes)} identical"

    if len(hashes) >= loop_window:
        window = hashes[-loop_window:]
        for period in range(1, loop_window // 2 + 1):
            is_loop = all(window[i] == window[i % period]
                          for i in range(len(window)))
            if is_loop and period < len(window):
                return False, f"A,B,A loop detected (period={period})"

    return True, "ok"


def scan_injection(text):
    """Scan user/model text for prompt-injection patterns."""
    if not text:
        return True, "empty"
    match = INJECTION_RX.search(text)
    if match:
        return False, f"injection pattern: {match.group()!r}"
    return True, "clean"


def check_verdict(text, vocab=None):
    """Extract and validate a verdict from model output against the vocabulary."""
    allowed = vocab or VERDICTS
    if not text:
        return False, "empty verdict"
    first_word = text.strip().split()[0].upper().rstrip(".,!?")
    if first_word not in allowed:
        return False, f"verdict {first_word!r} not in vocabulary"
    return True, first_word


def pre_motion_checklist(validator):
    """Battery + servo-sanity check on live telemetry. Degrades conservative."""
    details = {}

    battery = validator.read_battery()
    if battery is None:
        details["battery"] = "unreachable — assuming worst"
        return False, details
    details["battery"] = f"{battery:.1f}V"

    floor = getattr(validator, "battery_floor", 15)
    if battery < floor:
        details["gate"] = f"battery {battery:.1f}V below floor {floor}"
        return False, details

    joints = validator.read_joints()
    if joints is None:
        details["joints"] = "unreachable — assuming fault"
        return False, details
    details["joints"] = "readable"

    details["gate"] = "pass"
    return True, details


def encode_sensor_string(battery_pct, roll_byte, pitch_byte, servo_flags="OK"):
    """Encode telemetry as the compact S=XX.YY.ZZ.FF string the model sees."""
    return f"S={battery_pct:02X}.{roll_byte:02X}.{pitch_byte:02X}.{servo_flags}"
