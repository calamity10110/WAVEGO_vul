"""On-robot latency benchmark for the VLM control cycle.

Measures capture / inference / post-validation stage times per cycle and
checks the deployment gate: mean total cycle <= 2.5 s. Run on the robot:

    python -m vlm_ctrl.benchmark                    # real engine, live feed
    python -m vlm_ctrl.benchmark --mock             # fake engine (plumbing only)
    python -m vlm_ctrl.benchmark --static-frame f.jpg --mock

Static-frame mode reads a local JPEG instead of the live stream, so the
plumbing can be smoke-tested anywhere; hashes stay constant by design.
"""

import argparse
import hashlib
import os
import statistics
import time

import yaml

from .coherence import coherent
from .contracts import derive_command, validate_decision
from .engines import FastEngine
from .verify import SoftwareVerifier

HERE = os.path.dirname(os.path.abspath(__file__))
GATE_MS = 2500.0


def read_rss_kb():
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def mock_predict_fn(request):
    return {"answers": {
        "action": {"decision": "forward", "confidence": 0.9},
        "goal_status": {"decision": "in_progress", "confidence": 0.95},
    }}


def build_engine(cfg, mock, timer):
    checkpoint = cfg["fast_engine"]["checkpoint_dir"]
    if mock:
        return FastEngine(checkpoint, HERE, predict_fn=mock_predict_fn), 0.0
    engine = FastEngine(checkpoint, os.path.join(HERE, "media_frames"),
                        device=cfg["fast_engine"].get("device", "cpu"))
    t0 = timer()
    engine.load()
    return engine, (timer() - t0) * 1000.0


def make_frame_source(cfg, static_frame):
    if static_frame:
        with open(static_frame, "rb") as fh:
            data = fh.read()
        path = os.path.join(ensure_media(), "frame_latest.jpg")
        with open(path, "wb") as fh:
            fh.write(data)

        def source():
            return {"path": path, "bytes": len(data),
                    "hash": hashlib.md5(data).hexdigest()}
        return source

    from .capture import grab_frame
    cap = cfg.get("capture", {})
    url = cfg.get("frame_url", "http://127.0.0.1:5000/video_feed")
    out = os.path.join(ensure_media(), "frame_latest.jpg")

    def source():
        return grab_frame(url, out, resize_px=cap.get("resize_px", 448),
                          jpeg_quality=cap.get("jpeg_quality", 60))
    return source


def ensure_media():
    d = os.path.join(HERE, "media_frames")
    os.makedirs(d, exist_ok=True)
    return d


def run_cycle(engine, source, goal, verifier):
    t0 = time.perf_counter()
    frame = source()
    t1 = time.perf_counter()
    raw = engine.decide(goal, frame["path"])
    t2 = time.perf_counter()
    clean, errors = validate_decision(raw)
    if errors:
        raise RuntimeError(f"engine produced invalid decision: {errors}")
    ok, note = coherent(clean["label"], clean["reason"])
    cmd = derive_command(clean["label"])
    agrees, _, _ = verifier.verify(clean, frame["hash"])
    t3 = time.perf_counter()
    return {
        "capture_ms": round((t1 - t0) * 1000.0, 1),
        "infer_ms": round((t2 - t1) * 1000.0, 1),
        "post_ms": round((t3 - t2) * 1000.0, 1),
        "total_ms": round((t3 - t0) * 1000.0, 1),
        "label": clean["label"],
        "verifier": agrees,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="VLM cycle latency benchmark")
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--goal", default="find a ball and stop in front of it")
    ap.add_argument("--mock", action="store_true", help="fake engine, no model load")
    ap.add_argument("--static-frame", default=None, help="read this JPEG instead of the live feed")
    args = ap.parse_args(argv)

    with open(os.path.join(HERE, "config.yaml"), "r") as fh:
        cfg = yaml.safe_load(fh)

    engine, load_ms = build_engine(cfg, args.mock, time.perf_counter)
    source = make_frame_source(cfg, args.static_frame)
    verifier = SoftwareVerifier()

    rss0 = read_rss_kb()
    print(f"engine: {'MOCK' if args.mock else cfg['fast_engine']['checkpoint_dir']}")
    print(f"model load: {load_ms:.0f} ms   rss_before: {rss0} kB")

    for _ in range(args.warmup):
        run_cycle(engine, source, args.goal, verifier)

    rows = []
    for i in range(args.cycles):
        row = run_cycle(engine, source, args.goal, verifier)
        rows.append(row)
        print(f"cycle {i + 1:2d}: total {row['total_ms']:7.1f} ms "
              f"(capture {row['capture_ms']:6.1f}  infer {row['infer_ms']:7.1f}  "
              f"post {row['post_ms']:5.1f})  label={row['label']}")

    totals = [r["total_ms"] for r in rows]
    infers = [r["infer_ms"] for r in rows]
    caps = [r["capture_ms"] for r in rows]
    mean = statistics.mean(totals)
    p95 = sorted(totals)[max(0, int(len(totals) * 0.95) - 1)]
    rss1 = read_rss_kb()

    print("")
    print(f"mean total : {mean:8.1f} ms   (gate {GATE_MS:.0f} ms)")
    print(f"p95 total  : {p95:8.1f} ms")
    print(f"mean infer : {statistics.mean(infers):8.1f} ms")
    print(f"mean captur: {statistics.mean(caps):8.1f} ms")
    print(f"rss delta  : {rss1} kB (before {rss0} kB)")

    if mean <= GATE_MS:
        print(f"RESULT: PASS — loop cadence ~{mean / 1000.0:.2f}s is deployable")
        return 0
    print("RESULT: FAIL — set capture.resize_px: 224 in vlm_ctrl/config.yaml and re-run; "
          "if still failing, this host cannot close the control loop in software alone")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
