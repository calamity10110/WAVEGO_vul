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


def camera_num_for(port: str) -> int:
    """MIPI connector name -> picamera2 camera number. The Pi 5 has two
    connectors (silk-screened CAM/DISP 0 and 1); setup default is DSI1."""
    port = (port or "dsi1").lower()
    if port == "dsi0":
        return 0
    if port == "dsi1":
        return 1
    raise ValueError(f"unknown camera port: {port!r} (use dsi0 or dsi1)")


def fit_square(frame):
    """Square a frame without distorting it (TinyVLM preprocessing rule):
    wide frames center-crop, tall frames center-crop too; already-square
    frames pass through. Keeps the VLM's vision-token budget small and
    avoids letterboxing artefacts."""
    import numpy as np

    h, w = frame.shape[:2]
    if h == w:
        return frame
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    return np.ascontiguousarray(frame[y0:y0 + side, x0:x0 + side])


class CameraFrames:
    """Live picamera2 source. start() imports picamera2 lazily so the package
    imports on any machine; capture happens on a tiny thread at ~2 fps."""

    def __init__(self, width: int = 448, height: int = 448,
                 jpeg_quality: int = 80, interval_s: float = 0.5,
                 port: str = "dsi1"):
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.interval_s = interval_s
        self.camera_num = camera_num_for(port)
        self._jpeg: bytes | None = None
        self._lock = threading.Lock()
        self._cam = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        from picamera2 import Picamera2  # apt python3-picamera2; lazy by design

        self._cam = Picamera2(camera_num=self.camera_num)
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
                frame = fit_square(self._cam.capture_array())
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


class FrameChangeDetector:
    """Change-gated inference: skip the VLM when the scene has not moved.

    Compares a tiny grayscale thumbnail of each frame against the previous
    one (mean absolute difference, 0..255 scale). The pet spends most of
    its life in a static room — full inference per tick on an unchanged
    scene is the cheapest thing to eliminate.

    `changed_frame()` takes a raw capture array (numpy, CI-safe);
    `changed_jpeg()` decodes a JPEG first (needs cv2, Pi-only path).
    """

    def __init__(self, threshold: float = 3.0, size: int = 32):
        self.threshold = threshold
        self.size = size
        self._prev = None

    def changed_frame(self, frame) -> bool:
        small = _thumb(frame, self.size)
        if self._prev is None or small.shape != self._prev.shape:
            self._prev = small
            return True
        import numpy as np

        diff = float(np.abs(small.astype(int) - self._prev.astype(int)).mean())
        self._prev = small
        return diff > self.threshold

    def changed_jpeg(self, jpeg_bytes: bytes) -> bool:
        try:
            import cv2
            import numpy as np
        except ImportError:
            return True   # cannot decode without cv2: assume scene changed
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8),
                             cv2.IMREAD_COLOR)
        if frame is None:
            return True
        return self.changed_frame(frame)


def _thumb(frame, size: int):
    """Grayscale thumbnail via pure numpy — must not need cv2 so the
    change gate works (and tests run) on any box."""
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim == 3:
        arr = arr[..., :3]
        # BT.601 luma; float so the later abs-diff is smooth
        gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    else:
        gray = arr.astype(np.float32)
    h, w = gray.shape
    if h < size or w < size:
        rows = np.linspace(0, h - 1, size).astype(int)
        cols = np.linspace(0, w - 1, size).astype(int)
        return gray[np.ix_(rows, cols)]
    ch, cw = h // size, w // size
    return gray[:ch * size, :cw * size] \
        .reshape(size, ch, size, cw).mean(axis=(1, 3))
