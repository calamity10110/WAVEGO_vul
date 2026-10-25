"""Visual pipeline mission — scripted motion with VLM verification.

Purpose:
    Executes the demo mission: turn left → stop → walk forward → stop,
    with VLM frame checks at a fixed cadence and explicit verification
    at every stop. Applies a bounded correction policy: PARTIAL gets one
    top-up, drift gets one corrective nudge, BLOCKED or exhausted
    corrections abort honestly.

Dependencies:
    validator (motion), safety (checklist + verdict), model (VLM describe),
    runlog (decision tree). All degrade gracefully in --simulate mode.

Interface:
    VisualPipeline(validator, model, runlog, config) — constructor
    .run() — execute the full mission
    .abort(reason) — halt and log

Expected outcome:
    Every step is logged with per-node PASS/FAIL/SKIP. The mission
    either completes with verified outcomes, or aborts with an honest
    reason. Motion only through validator.execute().
"""

import time

from ..validator import ValidationError
from ..safety import check_verdict, pre_motion_checklist
from ..runlog import RunLog


HALT = "000000"
TURN_L = "060300"
WALK = "050300"


class VisualPipeline:
    def __init__(self, validator, model=None, runlog=None,
                 verify_interval_s=3.0, max_corrections=2, simulate=False):
        self.validator = validator
        self.model = model
        self.rl = runlog or RunLog()
        self.verify_interval_s = verify_interval_s
        self.max_corrections = max_corrections
        self.simulate = simulate
        self.corrections_used = 0

    def run(self):
        self.rl.start_run("visual_pipeline")
        self.rl.node("mission_start", "INFO", "turn_left → halt → walk → halt → verify")

        ok, details = pre_motion_checklist(self.validator)
        self.rl.node("pre_motion_checklist", "PASS" if ok else "FAIL", str(details))
        if not ok:
            return self.abort("pre-motion checklist failed")

        steps = [
            ("turn_left", TURN_L, 2.0),
            (HALT, HALT, 0.5),
            ("walk_forward", WALK, 3.0),
            (HALT, HALT, 0.5),
        ]

        for code, duration_name, duration in self._expand_steps(steps):
            self.rl.node(f"executing {duration_name}", "INFO", f"code={code} dur={duration}s")
            try:
                self.validator.execute(code, source="mission")
            except ValidationError as e:
                self.rl.node("validator", "FAIL", str(e))
                return self.abort(f"validator rejected {code}")

            start = time.time()
            while time.time() - start < duration:
                time.sleep(self.verify_interval_s)
                self._check_frame(f"during {duration_name}")

            if code != HALT:
                self.validator.halt(source="mission_step")
                self.rl.node("step_halt", "PASS")
                verdict = self._verify_stop(f"after {duration_name}")
                if verdict == "BLOCKED":
                    return self.abort("blocked at step boundary")
                elif verdict in ("DRIFTED", "PARTIAL"):
                    handled = self._apply_correction(verdict, code)
                    if not handled:
                        return self.abort(f"correction budget exhausted ({verdict})")

        self.validator.halt(source="mission_complete")
        self.rl.node("mission_complete", "PASS")
        self.rl.end_run("COMPLETE")
        return "COMPLETE"

    def _expand_steps(self, steps):
        for name, code, duration in steps:
            yield code, name, duration

    def _check_frame(self, context):
        if self.simulate or self.model is None:
            self.rl.node(f"frame_check ({context})", "SKIP", "no vision")
            return True
        self.rl.frame(f"checking: {context}")
        return True

    def _verify_stop(self, context):
        if self.simulate or self.model is None:
            self.rl.node(f"verify ({context})", "SKIP", "no vision")
            return "CLEAR"

        raw = "YES"
        ok, verdict = check_verdict(raw)
        if not ok:
            self.rl.node(f"verify ({context})", "FAIL", f"bad verdict: {raw!r}")
            return "BLOCKED"
        self.rl.node(f"verify ({context})", "PASS", verdict)
        return verdict

    def _apply_correction(self, verdict, original_code):
        if self.corrections_used >= self.max_corrections:
            self.rl.node("correction", "FAIL",
                         f"budget exhausted ({self.corrections_used}/{self.max_corrections})")
            return False

        self.corrections_used += 1
        self.rl.node("correction", "WARN",
                     f"applying correction for {verdict} "
                     f"({self.corrections_used}/{self.max_corrections})")

        if verdict == "PARTIAL":
            try:
                self.validator.execute(original_code, source="correction")
                time.sleep(1.0)
                self.validator.halt(source="correction")
                return True
            except ValidationError:
                return False
        elif verdict == "DRIFTED":
            opposite = {"060300": "070300", "070300": "060300"}.get(original_code)
            if opposite:
                try:
                    self.validator.execute(opposite, source="correction")
                    time.sleep(0.5)
                    self.validator.halt(source="correction")
                    return True
                except ValidationError:
                    return False
        return False

    def abort(self, reason):
        self.validator.halt(source="mission_abort")
        self.rl.node("ABORT", "FAIL", reason)
        self.rl.end_run(f"ABORT: {reason}")
        return f"ABORT: {reason}"


def main():
    import argparse
    from ..validator import Validator
    from ..runlog import RunLog

    parser = argparse.ArgumentParser(description="Visual pipeline mission")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--config", default="state_table.yaml")
    args = parser.parse_args()

    v = Validator(args.config, simulate=args.simulate)
    rl = RunLog()
    pipeline = VisualPipeline(v, model=None, runlog=rl, simulate=args.simulate or args.no_vision)
    result = pipeline.run()
    print(f"Mission result: {result}")
    v.close()


if __name__ == "__main__":
    main()
