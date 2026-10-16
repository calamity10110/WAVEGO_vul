"""Camera frame sources — keep ONLY the latest frame (spec rule: never
accumulate stale frames; the Pi's CPU cannot chew through a backlog).

CameraFrames wraps picamera2 (installed via apt on Raspberry Pi OS — never
pip) and hands out small JPEGs sized for the VLM's vision encoder. Downscale
at capture time: a 448px frame encodes several times faster on the Pi's CPU
than the camera's full 1536px output, with no behavioral loss for our use.
"""

from __future__ import annotations

import base64
import logging
import threading

log = logging.getLogger("wavego-pet")

# smallest usable JPEG (4x4 white, cv2-encoded) for tests and mock mode
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYF"
    "BgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoK"
    "CgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAAEAAQDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD9/KKK"
    "KAP/2Q=="
)


class CameraFrames:
    """Live picamera2 source. start() imports picamera2 lazily so the package
    imports on any machine; capture happens on a tiny thread at ~2 fps."""

    def __init__(self, width: int = 448, height: int = 448,
                 jpeg_quality: int = 80, interval_s: float = 0.5):
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.interval_s = interval_s
        self._jpeg: bytes | None = None
        self._lock = threading.Lock()
        self._cam = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        from picamera2 import Picamera2  # apt python3-picamera2; lazy by design

        self._cam = Picamera2()
        cfg = self._cam.create_video_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"})
        self._cam.configure(cfg)
        self._cam.start()
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop,
                                        daemon=True, name="pet-camera")
        self._thread.start()

    def _capture_loop(self) -> None:
        import time

        while self._running:
            try:
                frame = self._cam.capture_array()
                jpeg = _encode_jpeg(frame, self.jpeg_quality)
                with self._lock:
                    self._jpeg = jpeg
            except Exception as exc:
                log.warning("camera capture failed: %s", exc)
            time.sleep(self.interval_s)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:
                pass


def _encode_jpeg(frame, quality: int) -> bytes:
    import cv2  # apt python3-opencv

    ok, buf = cv2.imencode(".jpg", frame,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return buf.tobytes()


class StaticFrames:
    """Test/mock source: always returns the same (or cycled) JPEG. An empty
    list means 'camera produces nothing' — latest_jpeg() returns None."""

    def __init__(self, jpegs: list[bytes] | None = None):
        self._jpegs = jpegs if jpegs is not None else [TINY_JPEG]
        self._i = 0

    def latest_jpeg(self) -> bytes | None:
        if not self._jpegs:
            return None
        jpeg = self._jpegs[min(self._i, len(self._jpegs) - 1)]
        self._i = (self._i + 1) % len(self._jpegs)
        return jpeg
