"""Software verification: calibrated thresholds + temporal claim persistence.

A reached/impossible claim is only believed when it appears with high
calibrated confidence on two DIFFERENT frames (consecutive verify cycles) —
a single-frame claim is "pending", never a strike. When an online verifier
(engines.OnlineVerifier) is configured and reachable it is consulted first;
this module is the always-available fallback.
"""

CLAIMS = ("reached", "impossible")


class SoftwareVerifier:
    def __init__(self, claim_conf_min=0.75, act_conf_min=0.75):
        self.claim_conf_min = claim_conf_min
        self.act_conf_min = act_conf_min
        self._last_claim = None  # (frame_hash, status)

    def verify(self, decision, fhash):
        """Return (agrees, method, note); agrees may be None (= pending)."""
        status = decision["goal_status"]
        id_conf = float(decision.get("_id_conf", 0.0))

        if status in CLAIMS:
            prev = self._last_claim
            self._last_claim = (fhash, status)
            if id_conf < self.claim_conf_min:
                return False, "software", f"claim confidence {id_conf:.2f} < {self.claim_conf_min}"
            if prev is None or prev[1] != status:
                return None, "software", "claim awaiting second frame"
            if prev[0] == fhash:
                return None, "software", "claim frame unchanged"
            return True, "software", f"claim '{status}' persisted across frames"

        if decision["confidence"] == "low" or id_conf < self.act_conf_min:
            return False, "software", f"action confidence {id_conf:.2f} too low"

        return True, "software", "thresholds met"
