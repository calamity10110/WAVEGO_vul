"""Agent loop — the L0→L4 heartbeat.

Purpose:
    Runs the full pipeline cycle: ingest (L0) → perceive (L1) →
    deliberate (L2) → execute (L3) → outputs (L4). Includes a per-cycle
    error guard (halt + continue; 5 consecutive failures → clean exit)
    and a software watchdog. This is the main entry point.

Dependencies:
    ingest, perceive, deliberate, execute, outputs, validator, model, tools.

Interface:
    AgentLoop(cfg) — constructor
    .run(forever=True) — start the loop
    .run_once() — single cycle (for testing / --once)

Expected outcome:
    Each cycle produces a decision logged to agent.jsonl. Motion only
    occurs via validator.execute() through the set_state tool.
"""

import json
import os
import time

from .ingest import Ingest
from .perceive import Perceive
from .deliberate import Deliberate
from .execute import Execute
from .outputs import Outputs
from .validator import Validator, ValidationError
from .model import VLModel, ModelError
from .tools import ToolRunner, TOOL_SCHEMAS, ToolError

MAX_CONSECUTIVE_FAILURES = 5


class AgentLoop:
    def __init__(self, config_path="state_table.yaml", simulate=False,
                 once=False, log_dir="logs", validator=None, learner=None):
        self._owns_validator = validator is None
        self.validator = validator or Validator(config_path, simulate=simulate)
        if learner is None:
            import yaml
            with open(config_path, "r", encoding="utf-8") as fh:
                file_cfg = yaml.safe_load(fh) or {}
            from .learner import Learner
            learner = Learner(self.validator, file_cfg)
        self.learner = learner
        self.model = VLModel(
            url="http://127.0.0.1:8080",
            timeout_s=120)
        self.tools = ToolRunner(self.validator)
        self.ingest = Ingest()
        self.perceive = Perceive(self.model)
        self.deliberate = Deliberate(self.model, TOOL_SCHEMAS)
        self.execute = Execute(self.tools)
        self.outputs = Outputs(log_dir=log_dir)
        self.once = once
        self.consecutive_failures = 0
        self.cycle_count = 0

    def run(self):
        self.running = True
        self.outputs.log("info", "agent loop starting")
        try:
            while self.running:
                try:
                    self.run_once()
                    self.learner.observe("cycle", "ok", source="loop",
                                         outcome="ok")
                    self.learner.idle_pass()
                    self.consecutive_failures = 0
                    if self.once:
                        break
                except Exception as e:
                    self.consecutive_failures += 1
                    self.learner.observe("cycle", f"error: {type(e).__name__}",
                                         source="loop", outcome="fail")
                    self.outputs.log("error", f"cycle {self.cycle_count}: {e}")
                    try:
                        self.validator.halt(source="loop_guard")
                    except Exception:
                        pass
                    if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        self.outputs.log("critical",
                                         f"{MAX_CONSECUTIVE_FAILURES} consecutive failures — exiting")
                        break
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.outputs.log("info", "keyboard interrupt — halting")
        finally:
            try:
                self.validator.halt(source="shutdown")
            except Exception:
                pass
            if self._owns_validator:
                self.validator.close()
            self.outputs.log("info", f"agent loop stopped after {self.cycle_count} cycles")

    def run_once(self):
        self.cycle_count += 1
        cycle_id = f"c{self.cycle_count}"

        user_input = self.ingest.get_user_input()
        mission = self.ingest.get_mission()
        frame = self.ingest.get_frame()

        if user_input and user_input.get("intent") == "__ESTOP__":
            result = self.validator.halt(source="estop")
            self.outputs.log(cycle_id, f"ESTOP -> {result}")
            return

        perception = self.perceive.process(frame, user_input)
        decision = self.deliberate.decide(perception, mission)
        actions = self.execute.dispatch(decision)
        self.outputs.log(cycle_id, json.dumps({
            "perception": perception.get("summary", ""),
            "decision": decision.get("action", "none"),
            "actions": [a.get("name", "?") for a in actions if isinstance(a, dict)],
        }))
        return actions


def main():
    import argparse
    parser = argparse.ArgumentParser(description="WAVEGO Agent Loop")
    parser.add_argument("--simulate", action="store_true", help="no hardware")
    parser.add_argument("--once", action="store_true", help="single cycle")
    parser.add_argument("--config", default="state_table.yaml")
    args = parser.parse_args()

    loop = AgentLoop(config_path=args.config, simulate=args.simulate, once=args.once)
    loop.run()


if __name__ == "__main__":
    main()
