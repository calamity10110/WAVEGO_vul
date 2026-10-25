"""Runlog — decision-tree markdown logs.

Purpose:
    Writes per-run markdown logs to logs/DDMMYYYY.md showing the full
    pipeline decision tree with per-node annotations. Run 1 of the day
    gets verbose per-node output; subsequent runs get compact one-liners.
    Frame descriptions are pruned to keep files bounded.

Dependencies:
    stdlib only (os, time, json, textwrap).

Interface:
    RunLog(log_dir) — constructor
    .start_run(name) -> str — begin a run, returns run_id
    .node(name, status, detail) — annotate one pipeline node
    .frame(desc, max_len) — record a vision description (pruned)
    .end_run(outcome) — close the run
    .line(text) — raw line to the current log

Expected outcome:
    A markdown file per day with a readable decision tree per run.
    Every node is annotated: PASS/FAIL/SKIP — the log proves what was
    considered, not just what happened.
"""

import os
import time

SYMBOLS = {"PASS": "\u2713", "FAIL": "\u2717", "SKIP": "\u2298",
           "INFO": "\u2139", "WARN": "\u26a0"}


class RunLog:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._path = None
        self._run_count = 0
        self._frame_count = 0
        self._max_frames = 40
        self._max_node_entries = 500
        self._node_count = 0

    def start_run(self, name="unnamed"):
        date_str = time.strftime("%d%m%Y")
        self._path = os.path.join(self.log_dir, f"{date_str}.md")
        self._run_count = self._count_runs_today() + 1
        self._frame_count = 0
        self._node_count = 0

        verbose = self._run_count == 1
        header = f"\n## Run {self._run_count}: {name} ({time.strftime('%H:%M:%S')})\n"
        if verbose:
            header += "```\n"
        self._write(header)
        return f"r{self._run_count}"

    def node(self, name, status="INFO", detail=""):
        self._node_count += 1
        if self._node_count > self._max_node_entries:
            return
        sym = SYMBOLS.get(status, "\u2022")
        line = f"  {sym} {name}"
        if detail and self._run_count == 1:
            import textwrap
            wrapped = textwrap.fill(
                detail, width=72, initial_indent="      ", subsequent_indent="      ")
            line += f"\n{wrapped}"
        elif detail:
            line += f" — {detail[:80]}"
        self._write(line)

    def frame(self, desc, max_len=120):
        self._frame_count += 1
        if self._frame_count > self._max_frames:
            return
        pruned = desc[:max_len] + "..." if len(desc) > max_len else desc
        self._write(f"  \U0001f4f8 [{self._frame_count}] {pruned}")

    def end_run(self, outcome="done"):
        if self._run_count == 1:
            self._write("```")
        self._write(f"  \u2192 {outcome}\n")

    def line(self, text):
        self._write(f"  {text}")

    def _count_runs_today(self):
        if not os.path.exists(self._path):
            return 0
        with open(self._path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.startswith("## Run"))

    def _write(self, text):
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
