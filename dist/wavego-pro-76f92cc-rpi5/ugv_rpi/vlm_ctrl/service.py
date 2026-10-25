"""VLM control service — the goal-session runtime.

Owns the onboard Intern-Decision engine (loaded once), runs goal sessions
from a FIFO queue, exposes a stdlib HTTP API on :5001:

    GET  /api/vlm         status (session state, queue, log tail)
    POST /api/vlm         {"goal": "..."} or {"goals": ["...", ...]}
    POST /api/vlm/stop    stop robot motion + end current session

Runs as a separate process (systemd) so torch never loads into app.py.
Frame source is the main app's MJPEG feed; dispatch goes back through the
main app's /api/cmd, so the UART keeps a single owner.
"""

import base64
import json
import os
import queue as queue_mod
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from .capture import grab_frame, ensure_media_dir
from .coherence import coherent
from .contracts import validate_decision, derive_command
from .dispatch import Dispatcher
from .engines import FastEngine, OnlineVerifier
from .goal_loop import GoalLoop, RUNNING
from .verify import SoftwareVerifier

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.yaml"), "r") as fh:
    CFG = yaml.safe_load(fh)


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


class VlmService:
    def __init__(self, cfg=CFG):
        self.cfg = cfg
        loop_cfg = cfg.get("loop", {})
        self.loop_kwargs = dict(
            max_cycles=loop_cfg.get("max_cycles", 60),
            max_seconds=loop_cfg.get("max_seconds", 180.0),
            stall_cycles=loop_cfg.get("stall_cycles", 6),
            verify_every_n=loop_cfg.get("verify_every_n", 4),
        )
        self.cycle_min_s = float(loop_cfg.get("cycle_min_s", 2.5))
        self.media_dir = ensure_media_dir(HERE)
        self.engine = FastEngine(
            checkpoint_dir=cfg["fast_engine"]["checkpoint_dir"],
            media_root=self.media_dir,
            device=cfg["fast_engine"].get("device", "cpu"))
        ov = cfg.get("online_verifier", {})
        self.online = OnlineVerifier(
            base_url=ov.get("base_url", ""), model=ov.get("model", ""),
            api_key=os.environ.get("VLM_ONLINE_API_KEY", ""),
            timeout_s=float(ov.get("timeout_s", 10.0)),
            send_images=bool(ov.get("send_images", False)))
        self.software = SoftwareVerifier(
            claim_conf_min=float(cfg.get("verification", {}).get("claim_conf_min", 0.75)),
            act_conf_min=float(cfg.get("verification", {}).get("act_conf_min", 0.75)))
        self.dispatcher = Dispatcher(
            cfg.get("api_base_robot", "http://127.0.0.1:5000"),
            move_timeout_s=float(loop_cfg.get("move_timeout_s", 6.0)))
        self.goals = queue_mod.Queue()
        self.session = None
        self.session_lock = threading.Lock()
        self._stop_requested = False

    def start(self):
        self.engine.load()
        log("fast engine loaded")
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def enqueue(self, goals):
        for g in goals:
            self.goals.put(str(g)[:300])

    def request_stop(self):
        self._stop_requested = True
        with self.session_lock:
            if self.session is not None and self.session.state == RUNNING:
                self.session.request_stop()
        self.goals.put(None)
        self.dispatcher.stop_now()

    def _worker_loop(self):
        while True:
            goal = self.goals.get()
            if goal is None:
                self._stop_requested = False
                continue
            with self.session_lock:
                if self._stop_requested:
                    self._stop_requested = False
                    continue
                loop = GoalLoop(goal, **self.loop_kwargs)
                self.session = loop
            log("session start: " + goal)
            try:
                self._run_session(loop)
            except Exception as e:
                log(f"session error: {e}")
                self.dispatcher.stop_now()
            with self.session_lock:
                self.session = None
            log("session end")

    def _run_session(self, loop):
        pending = None
        feedback = None
        while True:
            self.dispatcher.tick()
            if loop.state != RUNNING:
                self._finish(loop, {"terminal": loop.state, "send_stop": True})
                return
            if pending is not None:
                frame = self._capture()
                verdict, method, note = self._verify(pending, frame)
                log(f"verify[{method}]: {note}")
                directive = loop.on_slow_verdict(pending, verdict, note)
                pending = None
            else:
                gate = loop.start_cycle()
                if not gate.get("run"):
                    self._finish(loop, gate)
                    return
                frame = self._capture()
                engine_name = loop.next_attempt_engine()
                decision, failure = self._decide(loop.goal, frame, feedback)
                if decision is None:
                    directive = loop.on_attempt_failed(
                        failure["errors"], failure.get("raw", "engine error"))
                    fb = directive.get("feedback")
                    feedback = ("prior attempt rejected: " + "; ".join(fb[1])) if fb else None
                else:
                    feedback = None
                    directive = loop.on_decision(decision)

            if directive.get("pending") or directive.get("confirm_slow"):
                pending = directive.get("decision")
            elif directive.get("dispatch"):
                self.dispatcher.send(derive_command(directive["dispatch"]))

            if directive.get("run") is False or directive.get("terminal"):
                self._finish(loop, directive)
                return
            time.sleep(self.cycle_min_s)

    def _decide(self, goal, frame, feedback):
        try:
            raw = self.engine.decide(goal, frame["path"], feedback=feedback)
        except Exception as e:
            return None, {"errors": [f"engine error: {e}"]}
        clean, errors = validate_decision(raw)
        if errors:
            return None, {"errors": errors, "raw": raw}
        ok, note = coherent(clean["label"], clean["reason"])
        if not ok:
            return None, {"errors": ["incoherent: " + note], "raw": raw}
        clean["_frame_hash"] = frame["hash"]
        return clean, raw

    def _verify(self, decision, frame):
        image_b64 = None
        if self.online.enabled and self.online.send_images:
            with open(frame["path"], "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode()
        verdict, method, note = self.online.verify(
            self.session.goal if self.session else "", decision, image_b64)
        if verdict is None:
            verdict, method, note = self.software.verify(decision, frame["hash"])
        return verdict, method, note

    def _capture(self):
        cap = self.cfg.get("capture", {})
        path = os.path.join(self.media_dir, "frame_latest.jpg")
        return grab_frame(self.cfg.get("frame_url", "http://127.0.0.1:5000/video_feed"),
                          path, resize_px=cap.get("resize_px", 448),
                          jpeg_quality=cap.get("jpeg_quality", 60))

    def _finish(self, loop, directive):
        if directive.get("send_stop"):
            self.dispatcher.stop_now()
        log(f"terminal: {directive.get('terminal')} cycles={loop.cycles}")

    def status(self):
        with self.session_lock:
            s = self.session.status() if self.session else None
        return {
            "engine_loaded": self.engine._engine is not None or self.engine._predict_fn is not None,
            "session": s,
            "queue_len": self.goals.qsize(),
            "online_verifier": self.online.enabled,
        }

    def serve(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/vlm":
                    self._json(200, service.status())
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
                if self.path == "/api/vlm":
                    goals = body.get("goals") or ([body["goal"]] if body.get("goal") else None)
                    if not goals:
                        return self._json(400, {"error": "goal(s) required"})
                    service.enqueue(goals)
                    return self._json(200, {"success": True, "queued": len(goals)})
                if self.path == "/api/vlm/stop":
                    service.request_stop()
                    return self._json(200, {"success": True})
                self._json(404, {"error": "not found"})

            def log_message(self, fmt, *args):
                pass

        httpd = ThreadingHTTPServer(("0.0.0.0", int(self.cfg.get("service_port", 5001))), Handler)
        log("vlm service on :5001")
        httpd.serve_forever()


if __name__ == "__main__":
    service = VlmService()
    service.start()
    service.serve()
