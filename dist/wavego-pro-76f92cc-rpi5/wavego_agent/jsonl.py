"""Asynchronous JSONL event logging.

Commands, safety events and telemetry snapshots go through one background
writer so logging never blocks the motion loop. A None path gives a no-op
logger (tests, dry runs).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any


class JsonlLogger:

    def __init__(self, path: str | None):
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=4096)
        self._fh = open(path, "a", encoding="utf-8") if path else None
        self._thread = threading.Thread(target=self._run, daemon=True) if self._fh else None
        if self._thread:
            self._thread.start()

    def log(self, event: dict[str, Any]) -> None:
        if self._fh is None:
            return
        record = {"t": round(time.time(), 3), **event}
        try:
            self._queue.put_nowait(json.dumps(record, separators=(",", ":")))
        except queue.Full:
            pass  # drop logs before blocking the control loop

    def _run(self) -> None:
        while True:
            line = self._queue.get()
            if line is None:
                break
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=2.0)
        self._fh.close()
        self._fh = None
        self._thread = None
