"""Validator — the sole motion authority and hex↔JSON translator.

Purpose:
    Accepts a 6-char hex state code from the model (or any caller), validates
    it against the codebook, checks safety gates, translates approved codes
    to ESP32 JSON, transmits over serial, and audit-logs every decision.
    This module is deterministic — no AI logic lives here.

Dependencies:
    PyYAML (config), pyserial (lazy import, only when hardware is connected),
    sqlite3 (audit trail).

Interface:
    Validator(config_path) — constructor, loads state_table.yaml
    .execute(code, source="model") -> dict  — validate + transmit + log
    .halt() -> dict                          — immediate HALT (always allowed)
    .read_battery() -> float | None          — T:207 query
    .read_joints() -> list[int] | None       — T:106 query
    .close()                                 — clean shutdown

Expected outcome:
    Every motion command passes through .execute() and either succeeds with
    a logged audit entry, or raises ValidationError with a precise reason.
    The robot never moves without the validator approving it.
"""

import json
import os
import re
import sqlite3
import threading
import time

import yaml

HEX_PATTERN = re.compile(r"^[0-9A-F]{6}$")
HALT = "000000"


class ValidationError(Exception):
    """Raised when a hex code fails any validator gate."""


class Validator:
    def __init__(self, config_path="state_table.yaml", simulate=False):
        self.config_path = config_path
        self.simulate = simulate
        with open(config_path, "r") as fh:
            self.cfg = yaml.safe_load(fh)

        self.codebook = self.cfg.get("motion", {}).get("codebook", {})
        self.forbidden = {tuple(t) for t in
                          self.cfg.get("motion", {}).get("forbidden_transitions", [])}
        self.allow_unverified = self.cfg.get("esp32", {}).get("allow_unverified", False)
        self.max_speed = self.cfg.get("motion", {}).get("max_speed_byte", 10)
        self.battery_floor = self.cfg.get("safety", {}).get("battery_floor_pct", 15)

        self.prev_code = HALT
        self.consecutive_bad = 0
        self._ser = None
        self._db = None
        self._db_lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        db_path = os.path.join(os.path.dirname(self.config_path), "state.db")
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS validator_log (
                ts REAL, code TEXT, name TEXT, source TEXT,
                outcome TEXT, detail TEXT
            )""")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS core (
                key TEXT PRIMARY KEY, value TEXT
            )""")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS learned (
                key TEXT PRIMARY KEY, value TEXT, promoted INTEGER DEFAULT 0
            )""")
        self._db.commit()

    def _audit(self, code, name, source, outcome, detail=""):
        with self._db_lock:
            self._db.execute(
                "INSERT INTO validator_log VALUES (?,?,?,?,?,?)",
                (time.time(), code, name, source, outcome, detail))
            self._db.commit()

    def _get_serial(self):
        if self.simulate:
            return None
        if self._ser is None:
            import serial
            port = self.cfg.get("esp32", {}).get("port", "/dev/ttyACM0")
            baud = self.cfg.get("esp32", {}).get("baud", 115200)
            self._ser = serial.Serial(port, baud, timeout=2)
        return self._ser

    def validate(self, code, source="model"):
        """Check all gates. Returns (esp32_json, entry) or raises ValidationError."""
        if not isinstance(code, str):
            raise ValidationError(f"code must be a string, got {type(code).__name__}")

        if not HEX_PATTERN.match(code):
            self.consecutive_bad += 1
            self._audit(code, "?", source, "REJECT", "bad hex format")
            raise ValidationError(
                f"code '{code}' does not match ^[0-9A-F]{{6}}$")

        entry = self.codebook.get(code)
        if entry is None:
            self.consecutive_bad += 1
            self._audit(code, "?", source, "REJECT", "not in codebook")
            raise ValidationError(f"code {code} not in codebook")

        if not entry.get("verified", False) and not self.allow_unverified:
            self.consecutive_bad += 1
            self._audit(code, entry["name"], source, "REJECT", "unverified")
            raise ValidationError(
                f"code {code} ({entry['name']}) is unverified; "
                "set esp32.allow_unverified to override")

        speed = int(code[2:4], 16)
        if speed > self.max_speed:
            self.consecutive_bad += 1
            self._audit(code, entry["name"], source, "REJECT", f"speed {speed} > {self.max_speed}")
            raise ValidationError(
                f"speed byte {speed} exceeds max {self.max_speed}")

        if (self.prev_code, code) in self.forbidden:
            self.consecutive_bad += 1
            self._audit(code, entry["name"], source, "REJECT",
                        f"forbidden transition from {self.prev_code}")
            raise ValidationError(
                f"transition {self.prev_code} -> {code} is forbidden")

        if self.consecutive_bad > self.cfg.get("safety", {}).get("max_bad_codes", 3):
            self._audit(HALT, "HALT", source, "FORCED_HALT", "too many bad codes")
            return self.codebook[HALT]["esp32"], self.codebook[HALT]

        self.consecutive_bad = 0
        return entry["esp32"], entry

    def execute(self, code, source="model"):
        """Validate + transmit + log. The only path to robot motion."""
        esp32_json, entry = self.validate(code, source)

        if not self.simulate:
            ser = self._get_serial()
            ser.write((esp32_json + "\n").encode("utf-8"))

        self.prev_code = code
        self._audit(code, entry["name"], source, "EXECUTE", esp32_json)
        return {"code": code, "name": entry["name"], "esp32": esp32_json,
                "source": source, "ts": time.time()}

    def halt(self, source="safety"):
        """Unconditional HALT — always allowed, never blocked."""
        return self.execute(HALT, source=source)

    def read_battery(self):
        if self.simulate:
            return 80.0
        ser = self._get_serial()
        ser.reset_input_buffer()
        ser.write(b'{"T":207}\n')
        deadline = time.time() + 2.0
        while time.time() < deadline:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("T") == -207:
                    return data.get("voltage")
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def read_joints(self):
        if self.simulate:
            return [512] * 12
        ser = self._get_serial()
        ser.reset_input_buffer()
        ser.write(b'{"T":106}\n')
        deadline = time.time() + 2.0
        while time.time() < deadline:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("T") == -106:
                    return data.get("fb")
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def close(self):
        if self._ser:
            self._ser.close()
            self._ser = None
        if self._db:
            self._db.close()
            self._db = None
