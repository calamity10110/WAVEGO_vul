"""Outputs — L4 logging and state exposure.

Purpose:
    JSONL cycle log (logs/agent.jsonl) and in-memory state for the
    future dashboard WebSocket. Simple, reliable, no external deps.
"""

import json
import os
import time


class Outputs:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.jsonl_path = os.path.join(log_dir, "agent.jsonl")
        self.state = {}

    def log(self, cycle_id, message):
        entry = {"ts": time.time(), "cycle": cycle_id, "msg": message}
        with open(self.jsonl_path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        self.state[cycle_id] = message

    def get_state(self):
        return dict(self.state)
