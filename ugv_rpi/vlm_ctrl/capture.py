"""MJPEG frame capture from the robot's own /video_feed stream.

Connects to the localhost MJPEG endpoint and returns the first complete
JPEG (FFD8..FFD9 markers) — the newest frame at connection time. Optional
PIL downscale; best-effort, the frame is stored unresized when PIL is
absent. Marker-based parsing: an embedded-thumbnail FFD9 could truncate a
frame early — accepted for v1, the verifier's frame-hash change detection
tolerates a stale frame by design.
"""

import hashlib


def grab_frame(url, out_path, resize_px=None, jpeg_quality=None,
               timeout=4.0, getter=None):
    import requests
    do_get = getter or (lambda u, t: requests.get(u, stream=True, timeout=(2, t)))
    r = do_get(url, timeout)
    jpeg = None
    buf = bytearray()
    for chunk in r.iter_content(4096):
        buf.extend(chunk)
        start = buf.find(b"\xff\xd8")
        if start == -1:
            if len(buf) > 4 * 1024 * 1024:
                raise RuntimeError("no JPEG start marker in stream")
            continue
        end = buf.find(b"\xff\xd9", start)
        if end == -1:
            if len(buf) > 8 * 1024 * 1024:
                raise RuntimeError("no JPEG end marker in stream")
            continue
        jpeg = bytes(buf[start:end + 2])
        break
    r.close()
    if jpeg is None:
        raise RuntimeError("stream closed before a complete frame")

    if resize_px:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
            img.thumbnail((resize_px, resize_px))
            img.save(out_path, "JPEG", quality=jpeg_quality or 60)
        except ImportError:
            with open(out_path, "wb") as fh:
                fh.write(jpeg)
    else:
        with open(out_path, "wb") as fh:
            fh.write(jpeg)

    with open(out_path, "rb") as fh:
        stored = fh.read()
    return {"path": out_path, "bytes": len(stored),
            "hash": hashlib.md5(stored).hexdigest()}


def ensure_media_dir(base_dir):
    import os
    d = os.path.join(base_dir, "media_frames")
    os.makedirs(d, exist_ok=True)
    return d
