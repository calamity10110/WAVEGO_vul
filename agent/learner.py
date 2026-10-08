"""Learner — quarantine-first knowledge store with gated promotion.

Purpose:
    Collects observations about the robot and model behavior into a
    quarantine tier (`learned` table), and promotes entries to the
    trusted tier (`core` table) only after passing evidence gates:
    minimum trial count, failure tilt below threshold, and value
    consistency. Promotion NEVER grants motion authority: keys that
    look like hex state codes are refused, so learning can never grow
    the codebook or bypass the validator.

Dependencies:
    sqlite3 (state.db shared with the validator), threading, time.
    agent.safety.scan_injection (values arrive from model output).

Interface:
    Learner(validator, config) — reads the `learner:` section
    .observe(key, value, source, outcome) — quarantine an observation
    .stats() -> dict — per-key counts
    .eligible() -> list[str] — keys passing all gates
    .promote(key) -> bool — move a key to the trusted tier
    .trusted(key, default) — read only from the trusted tier
    .idle_pass() -> dict — bounded promotion review for idle time

Expected outcome:
    Observations accumulate in quarantine; promotion is rare, gated,
    and motion-inert. The core tier holds only operator-reviewable
    knowledge, never new state codes.
"""

import os
import re
import sqlite3
import threading
import time

from .safety import scan_injection

HEX_KEY = re.compile(r"^[0-9A-F]{6}$")


class Learner:
    def __init__(self, validator, config=None):
        self.validator = validator
        cfg = (config or {}).get("learner", {})
        self.enabled = cfg.get("enabled", True)
        self.min_trials = cfg.get("min_trials", 5)
        self.tilt_threshold = cfg.get("tilt_threshold", 0.4)
        self.promote_trials = cfg.get("promote_trials", 3)
        self.idle_budget_s = cfg.get("idle_budget_s", 5)
        self.idle_min_interval_s = cfg.get("idle_min_interval_s", 600)

        db_path = os.path.join(os.path.dirname(validator.config_path), "state.db")
        self._db = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
        self._lock = threading.Lock()
        self._last_idle_pass = 0.0
        with self._lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS learner_obs (
                    key TEXT, value TEXT, source TEXT,
                    outcome TEXT, ts REAL
                )""")
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_obs_key ON learner_obs(key)")
            self._db.commit()

    def observe(self, key, value, source="model", outcome="ok"):
        if not self.enabled:
            return False
        key = str(key)
        value = str(value)
        ok, _ = scan_injection(value)
        if not ok:
            return False
        with self._lock:
            self._db.execute(
                "INSERT INTO learner_obs (key, value, source, outcome, ts) "
                "VALUES (?,?,?,?,?)", (key, value, source, outcome, time.time()))
            self._db.execute(
                "INSERT INTO learned (key, value, promoted) VALUES (?,?,0) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            self._db.commit()
        return True

    def stats(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT key, COUNT(*) AS n, "
                "SUM(CASE WHEN outcome='ok' THEN 1 ELSE 0 END) AS ok_n, "
                "MAX(ts) FROM learner_obs GROUP BY key").fetchall()
        return {key: {"trials": n, "ok": ok_n, "fail": n - ok_n, "last_ts": ts}
                for key, n, ok_n, ts in rows}

    def eligible(self):
        result = []
        for key, s in self.stats().items():
            if HEX_KEY.match(key):
                continue
            if s["trials"] < self.min_trials:
                continue
            tilt = s["fail"] / s["trials"]
            if tilt >= self.tilt_threshold:
                continue
            if not self._consistent(key):
                continue
            result.append(key)
        return sorted(result)

    def _consistent(self, key):
        with self._lock:
            rows = self._db.execute(
                "SELECT value FROM learner_obs WHERE key=? "
                "ORDER BY ts DESC LIMIT ?", (key, self.promote_trials)).fetchall()
        values = {v for (v,) in rows}
        return len(rows) >= self.promote_trials and len(values) == 1

    def promote(self, key):
        key = str(key)
        if HEX_KEY.match(key):
            return False
        if key not in self.eligible():
            return False
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM learner_obs WHERE key=? "
                "ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
            if row is None:
                return False
            value = row[0]
            self._db.execute(
                "INSERT INTO core (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            self._db.execute(
                "UPDATE learned SET promoted=1 WHERE key=?", (key,))
            self._db.commit()
        return True

    def trusted(self, key, default=None):
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM core WHERE key=?", (str(key),)).fetchone()
        return row[0] if row else default

    def idle_pass(self, force=False):
        now = time.time()
        if not force and now - self._last_idle_pass < self.idle_min_interval_s:
            return {"skipped": "interval"}
        if not self.enabled:
            return {"skipped": "disabled"}
        self._last_idle_pass = now
        deadline = now + self.idle_budget_s
        promoted = []
        for key in self.eligible():
            if time.time() > deadline:
                break
            if self.promote(key):
                promoted.append(key)
        return {"promoted": promoted, "elapsed_s": round(time.time() - now, 3)}

    def close(self):
        with self._lock:
            self._db.commit()
            self._db.close()
