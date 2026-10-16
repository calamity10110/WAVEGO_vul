"""petserve — run the robot pet.

    python -m wavego_pet.petserve --config pet_config.yaml          # real robot
    python -m wavego_pet.petserve --mock --duration 60              # any dev box

--mock needs no camera, no llama-server and no UART: scripted scenes drive
the persona and a MockTransport records what would go down the wire.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import yaml

from wavego_agent.jsonl import JsonlLogger
from wavego_agent.supervisor import MotionSupervisor
from wavego_agent.telemetry import MockTelemetry
from wavego_agent.transport import MockTransport, SerialTransport

from .brain import PetBrain
from .persona import PersonaConfig, PetPersona, Scene
from .vlm_client import FakeVLM, VLMClient
from .vision import CameraFrames, StaticFrames

log = logging.getLogger("wavego-pet")

MOCK_SCRIPT = [
    Scene(),                                   # empty room
    Scene(person_visible=True, position="center", gesture="none"),    # you walk in -> greet
    Scene(person_visible=True, position="center", gesture="wave"),    # you wave -> trick
    Scene(person_visible=True, position="left", gesture="none"),      # you step left -> follow
    Scene(person_visible=True, position="center", gesture="none"),
    Scene(),                                   # you leave -> fidget, then sleep
]


def load_config(path: str | None) -> dict:
    import os

    cfg_path = path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "pet_config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_components(cfg: dict, mock: bool):
    persona_cfg = PersonaConfig(**(cfg.get("persona") or {}))
    persona = PetPersona(persona_cfg)
    if mock:
        transport = MockTransport()
        vlm = FakeVLM(list(MOCK_SCRIPT))
        frames = StaticFrames()
        telemetry = MockTelemetry()
    else:
        uart = cfg.get("uart") or {}
        transport = SerialTransport(uart.get("device"), uart.get("baud", 115200))
        vlm_cfg = cfg.get("vlm") or {}
        vlm = VLMClient(url=vlm_cfg.get("url", "http://127.0.0.1:8080"),
                        model=vlm_cfg.get("model", "qwen3-vl-2b"),
                        timeout_s=vlm_cfg.get("timeout_s", 120.0))
        cam_cfg = cfg.get("camera") or {}
        frames = CameraFrames(width=cam_cfg.get("width", 448),
                              height=cam_cfg.get("height", 448),
                              jpeg_quality=cam_cfg.get("jpeg_quality", 80))
        telemetry = None
    return transport, vlm, frames, telemetry, persona


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="petserve",
                                     description="WAVEGO robot pet")
    parser.add_argument("--config", default=None,
                        help="pet_config.yaml path (default: beside package)")
    parser.add_argument("--mock", action="store_true",
                        help="no camera/VLM/UART: scripted scenes + recording transport")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="seconds to run (0 = forever)")
    parser.add_argument("--scene-every", type=float, default=None,
                        help="override scene cadence (s)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s [pet] %(levelname)s %(message)s")
    cfg = load_config(args.config)

    transport, vlm, frames, telemetry, persona = build_components(cfg, args.mock)
    supervisor = MotionSupervisor(
        transport, telemetry=telemetry,
        logger=JsonlLogger((cfg.get("logging") or {}).get("jsonl_path")),
        rate_hz=(cfg.get("motion") or {}).get("rate_hz", 30.0))
    brain = PetBrain(
        supervisor, vlm, frames, persona,
        scene_every_s=args.scene_every
        or (cfg.get("brain") or {}).get("scene_every_s", 12.0))

    supervisor.start()
    if not args.mock:
        frames.start()
    log.info("pet is alive (mock=%s) — Ctrl-C to stop", args.mock)
    try:
        started = time.monotonic()
        while True:
            brain.tick()
            if args.duration and time.monotonic() - started >= args.duration:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("stopping pet")
    finally:
        if supervisor.estop_active:
            supervisor.clear_estop()
        supervisor.submit({"action": "sit"}) if not supervisor.estop_active else None
        time.sleep(1.0)                       # let the sit command play out
        supervisor.emergency_stop("shutdown")
        if not args.mock and frames is not None and hasattr(frames, "stop"):
            frames.stop()
        supervisor.stop()
        if args.mock:
            log.info("wire transcript:\n%s",
                     "\n".join(getattr(transport, "wire_lines", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
