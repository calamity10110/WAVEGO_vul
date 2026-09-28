"""Robot dispatch through the main app's bypass API, with a movement
timeout watchdog. Commands go to POST /api/cmd on localhost — app.py keeps
single UART ownership. T:111 gait commands are level-based, so every
movement dispatch re-arms a timer that sends stop when it expires.
"""

import threading
import time


class Dispatcher:
    def __init__(self, api_base, move_timeout_s=6.0, poster=None, clock=None):
        self.api_base = api_base.rstrip("/")
        self.move_timeout_s = move_timeout_s
        self._poster = poster or self._post
        self._clock = clock or time.monotonic
        self._deadline = None
        self._lock = threading.Lock()

    def _post(self, url, payload):
        import requests
        try:
            return requests.post(url, json=payload, timeout=2).ok
        except Exception:
            return False

    def send(self, cmd):
        """Send a raw command dict. Returns success bool. None = no-op."""
        if cmd is None:
            return True
        ok = self._poster(self.api_base + "/api/cmd", cmd)
        if ok and cmd.get("T") == 111 and (cmd.get("FB") or cmd.get("LR")):
            with self._lock:
                self._deadline = self._clock() + self.move_timeout_s
        elif ok:
            with self._lock:
                self._deadline = None
        return ok

    def tick(self):
        """Fire the movement timeout if expired. Returns True if a stop was sent."""
        with self._lock:
            deadline = self._deadline
            if deadline is not None and self._clock() >= deadline:
                self._deadline = None
            else:
                return False
        return self.send({"T": 111, "FB": 0, "LR": 0})

    def stop_now(self):
        with self._lock:
            self._deadline = None
        return self.send({"T": 111, "FB": 0, "LR": 0})
