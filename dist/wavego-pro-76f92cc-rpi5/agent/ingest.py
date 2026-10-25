"""Ingest — L0 input collection.

Purpose:
    Collects camera frames, user input (bounded queue), and mission
    files (mtime-cached). Pure input — no processing, no decisions.

Dependencies:
    stdlib; picamera2 (lazy import on Pi).
"""

import os
import queue
import time


class Ingest:
    def __init__(self, max_queue=8):
        self.user_queue = queue.Queue(maxsize=max_queue)
        self._mission_cache = None
        self._mission_mtime = 0
        self._camera = None

    def submit_user_input(self, text):
        try:
            self.user_queue.put_nowait({"text": text, "ts": time.time()})
        except queue.Full:
            try:
                self.user_queue.get_nowait()
                self.user_queue.put_nowait({"text": text, "ts": time.time()})
            except (queue.Empty, queue.Full):
                pass

    def get_user_input(self):
        try:
            return self.user_queue.get_nowait()
        except queue.Empty:
            return None

    def get_mission(self, path="mission.yaml"):
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        if mtime == self._mission_mtime and self._mission_cache is not None:
            return self._mission_cache
        self._mission_mtime = mtime
        import yaml
        with open(path) as fh:
            self._mission_cache = yaml.safe_load(fh)
        return self._mission_cache

    def get_frame(self):
        try:
            if self._camera is None:
                from picamera2 import Picamera2
                self._camera = Picamera2()
                self._camera.configure(
                    self._camera.create_preview_configuration(main={"size": (640, 480)}))
                self._camera.start()
                time.sleep(0.5)
            return self._camera.capture_array()
        except ImportError:
            return None
        except Exception:
            return None
