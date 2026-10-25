"""Serve — single-process host for the agent loop and the dashboard.

Purpose:
    Runs AgentLoop in a background thread and the FastAPI dashboard on
    uvicorn in the main thread, both sharing ONE Validator instance so
    the ESP32 UART keeps a single owner (serial discipline). The
    dashboard's estop halts through the same validator the loop uses.

Dependencies:
    agent.loop, agent.dashboard, agent.learner, agent.validator.

Interface:
    python -m agent.serve --token TOKEN [--simulate] [--config PATH]
        [--host H] [--port P] [--once]
    SIGINT/SIGTERM -> loop stop + validator.halt + clean close.
"""

import argparse
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.dashboard import create_app
from agent.learner import Learner
from agent.loop import AgentLoop
from agent.validator import Validator


def serve(config_path="state_table.yaml", token=None, host="0.0.0.0",
          port=8000, simulate=False, once=False, run_loop=True):
    import uvicorn
    import yaml

    with open(config_path, "r", encoding="utf-8") as fh:
        file_cfg = yaml.safe_load(fh) or {}
    dash_cfg = file_cfg.get("dashboard", {})
    host = host or dash_cfg.get("host", "0.0.0.0")
    port = port or dash_cfg.get("port", 8000)

    validator = Validator(config_path, simulate=simulate)
    learner = Learner(validator, file_cfg)

    loop = None
    loop_thread = None
    if run_loop:
        loop = AgentLoop(config_path, simulate=simulate, once=once,
                         validator=validator, learner=learner)
        loop_thread = threading.Thread(target=loop.run, name="agent-loop",
                                       daemon=True)
        loop_thread.start()

    app = create_app(validator, token)

    def shutdown(signum, frame):
        if loop is not None:
            loop.running = False
        validator.halt(source="serve_shutdown")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if loop is not None:
            loop.running = False
            loop_thread.join(timeout=5)
        validator.halt(source="serve_exit")
        learner.close()
        validator.close()


def main():
    parser = argparse.ArgumentParser(
        description="WAVEGO agent host: loop + dashboard, one validator")
    parser.add_argument("--config", default="state_table.yaml")
    parser.add_argument("--token", default=None,
                        help="dashboard auth token (or AGENT_TOKEN env var)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="run a single agent cycle")
    args = parser.parse_args()

    token = args.token or os.environ.get("AGENT_TOKEN")
    if not token:
        parser.error("no dashboard token: pass --token or set AGENT_TOKEN")

    serve(config_path=args.config, token=token, host=args.host,
          port=args.port, simulate=args.simulate, once=args.once)


if __name__ == "__main__":
    main()
