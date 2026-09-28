"""Goal-session state machine for VLM control loops.

Pure logic, no I/O — decisions are fed in, directives come out; the caller
owns HTTP, dispatch, and timing.

Cycle attempt ladder: fast -> fast+feedback -> slow+feedback -> STOP + abort
(3-strike rule). Claim verification: "reached"/"impossible" and low-confidence
fast decisions are only accepted after slow-path agreement. Stall detection:
the same label for `stall_cycles` consecutive in-progress cycles forces one
slow-path replan; if the stall then recurs for another `stall_cycles`, abort
with stop. Terminal REACHED always ends in a stop unless the final label is
already a safe pose (stop/none/sit/stand).
"""

import time

RUNNING = "RUNNING"
REACHED = "REACHED"
IMPOSSIBLE = "IMPOSSIBLE"
ABORTED_STRIKES = "ABORTED_STRIKES"
ABORTED_STALL = "ABORTED_STALL"
TIMEOUT = "TIMEOUT"
STOPPED_USER = "STOPPED_USER"

TERMINAL = {REACHED, IMPOSSIBLE, ABORTED_STRIKES, ABORTED_STALL, TIMEOUT, STOPPED_USER}

_ATTEMPT_LADDER = ("fast", "fast", "slow")
_STOP_CMD = {"T": 111, "FB": 0, "LR": 0}
_CLAIMS = ("reached", "impossible")
_SAFE_POSES = ("stop", "none", "sit", "stand")


class GoalLoop:
    def __init__(self, goal, max_cycles=60, max_seconds=180.0,
                 stall_cycles=6, verify_every_n=4, id_conf_min=0.6, now=None):
        self.goal = goal
        self.max_cycles = max_cycles
        self.max_seconds = max_seconds
        self.stall_cycles = stall_cycles
        self.verify_every_n = max(1, verify_every_n)
        self.id_conf_min = id_conf_min
        self.now = now or time.time
        self.state = RUNNING
        self.cycles = 0
        self.started_at = self.now()
        self.log = []
        self._attempt = 0
        self._recent_labels = []
        self._stall_replan_done = False

    def _t(self):
        return self.now()

    def start_cycle(self):
        if self.state != RUNNING:
            return {"run": False, "reason": self.state}
        if self.cycles >= self.max_cycles:
            return self._finish(TIMEOUT)
        if self._t() - self.started_at >= self.max_seconds:
            return self._finish(TIMEOUT)
        self._attempt = 0
        return {"run": True}

    def next_attempt_engine(self):
        if self.state != RUNNING or self._attempt >= len(_ATTEMPT_LADDER):
            return None
        return _ATTEMPT_LADDER[self._attempt]

    def on_attempt_failed(self, errors, raw_output):
        self._attempt += 1
        self.log.append({
            "t": round(self._t(), 3), "kind": "reject", "attempt": self._attempt,
            "errors": errors, "raw": str(raw_output)[:300],
        })
        if self._attempt >= len(_ATTEMPT_LADDER):
            self.log.append({"t": round(self._t(), 3), "kind": "abort", "reason": "3 strikes"})
            return self._finish(ABORTED_STRIKES)
        return {"retry": True, "engine": _ATTEMPT_LADDER[self._attempt],
                "feedback": (raw_output, errors, self.log[-1])}

    def on_decision(self, decision):
        """Feed a validated + coherent decision produced by next_attempt_engine()."""
        self.cycles += 1
        claim = decision["goal_status"] in _CLAIMS
        low_conf = decision.get("confidence") == "low"
        spot = self.cycles % self.verify_every_n == 0
        if claim or low_conf or spot:
            return {"dispatch": None, "confirm_slow": True, "decision": decision}
        return self._accept(decision)

    def on_slow_verdict(self, decision, agrees, note=""):
        """agrees: True/False verdict, or None = pending (claim not yet persistent).

        Pending defers without consuming a strike; the caller re-verifies on a
        fresh frame and calls this again with the same decision.
        """
        if agrees is None:
            return {"pending": True, "decision": decision}
        if agrees:
            return self._accept(decision)
        return self.on_attempt_failed(["slow-path verifier disagreed: " + note], decision)

    def request_stop(self):
        return self._finish(STOPPED_USER)

    def status(self):
        return {
            "goal": self.goal, "state": self.state, "cycles": self.cycles,
            "elapsed_s": round(self._t() - self.started_at, 2),
            "recent_labels": list(self._recent_labels[-self.stall_cycles:]),
            "log_tail": self.log[-8:],
        }

    def _accept(self, decision):
        label = decision["label"]
        self._recent_labels.append(label)
        stall = self._stall_check(decision["goal_status"])
        if stall == "replan":
            return {"dispatch": None, "confirm_slow": True,
                    "decision": decision, "reason": "stall_replan"}
        if stall == "abort":
            return self._finish(ABORTED_STALL)
        if decision["goal_status"] == "reached":
            return self._finish(REACHED, stop=label not in _SAFE_POSES)
        if decision["goal_status"] == "impossible":
            return self._finish(IMPOSSIBLE)
        return {"dispatch": label}

    def _stall_check(self, goal_status):
        if goal_status != "in_progress":
            self._stall_replan_done = False
            return None
        same_stall = (len(self._recent_labels) >= self.stall_cycles
                      and len(set(self._recent_labels[-self.stall_cycles:])) == 1)
        if not same_stall:
            return None
        if self._stall_replan_done:
            return "abort"
        self._stall_replan_done = True
        self._recent_labels = []
        return "replan"

    def _finish(self, terminal, stop=True):
        self.state = terminal
        directive = {"run": False, "terminal": terminal}
        if stop:
            directive["send_stop"] = True
            directive["stop_cmd"] = dict(_STOP_CMD)
        return directive
