"""petserve — run the robot pet.

    python -m wavego_pet.petserve --config pet_config.yaml          # real robot
    python -m wavego_pet.petserve --mock --duration 60              # any dev box

--mock needs no camera, no llama-server and no UART: scripted scenes drive
the persona and a MockTransport records what would go down the wire.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time

import yaml

from wavego_agent.jsonl import JsonlLogger
from wavego_agent.supervisor import MotionSupervisor
from wavego_agent.telemetry import MockTelemetry
from wavego_agent.transport import MockTransport, SerialTransport, resolve_serial_device

from .brain import PetBrain
from .display import LCDDisplay, NullDisplay, detect_dsi_display
from .persona import PersonaConfig, PetPersona, Scene
from .systemstats import SystemStats
from .validator import ConfigError, build_validator, validate_llm_config
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
        device = resolve_serial_device(uart.get("device", "auto"))
        log.info("ESP32 control board on %s (USB or GPIO UART, auto-resolved)",
                 device)
        transport = SerialTransport(device, uart.get("baud", 115200))
        vlm_cfg = cfg.get("vlm") or {}
        vlm = VLMClient(url=vlm_cfg.get("url", "http://127.0.0.1:8080"),
                        model=vlm_cfg.get("model", "qwen3-vl-2b"),
                        timeout_s=vlm_cfg.get("timeout_s", 120.0),
                        api_key_env=vlm_cfg.get("api_key_env"))
        cam_cfg = cfg.get("camera") or {}
        frames = CameraFrames(width=cam_cfg.get("width", 448),
                              height=cam_cfg.get("height", 448),
                              jpeg_quality=cam_cfg.get("jpeg_quality", 80),
                              port=cam_cfg.get("port", "dsi1"))
        telemetry = None
    return transport, vlm, frames, telemetry, persona


def build_display(cfg: dict, mock: bool):
    """Use the DSI LCD only when configured AND attached. Port/size come
    from the installer's setup choices (camera takes one MIPI connector,
    the display must use the OTHER one)."""
    if mock:
        return NullDisplay()
    dcfg = cfg.get("display") or {}
    enabled = dcfg.get("enabled", "auto")
    dsi = detect_dsi_display()
    if enabled is True or (enabled == "auto" and dsi):
        if dsi:
            log.info("DSI display detected: %s (configured port %s)",
                     dsi, dcfg.get("port", "n/a"))
        return LCDDisplay(width=int(dcfg.get("width", 480)),
                          height=int(dcfg.get("height", 480)))
    if enabled == "auto":
        log.info("no DSI display detected — status UI skipped")
    return NullDisplay()


def _try_run(cmd: list[str], timeout_s: float = 15.0) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_s, check=False)
        return out.stdout.strip() or out.stderr.strip() or ""
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_doctor(cfg: dict) -> int:
    """Hardware preflight: camera port, ESP32 connection, VLM, DSI LCD."""
    print("wavego pet — hardware doctor")
    print("-" * 50)

    print("\n[camera] (CAM0/CAM1 — either MIPI connector works)")
    out = (_try_run(["rpicam-hello", "--list-cameras"])
           or _try_run(["rpicam-still", "--list-cameras"]))
    if out is None:
        print("  SKIP: rpicam tools not available (dev box?)")
    elif out and "no cameras" not in out.lower():
        print(f"  OK:\n{out}")
    else:
        print("  FAIL: no camera detected.")
        print("  - re-seat the ribbon: contacts toward the board, blue")
        print("    stiffener facing the latch; CAM0 and CAM1 both work —")
        print("    try the other connector, then re-run this doctor.")

    print("\n[ESP32 control board] (USB or GPIO UART)")
    uart_cfg = (cfg.get("uart") or {}).get("device", "auto")
    resolved = resolve_serial_device(uart_cfg)
    print(f"  resolved device: {resolved} (config: {uart_cfg})")
    if resolved.startswith(("/dev/ttyACM", "/dev/ttyUSB")):
        print("  OK: ESP32 connected over USB")
    else:
        print("  WARN: no USB serial found; using the GPIO UART.")
        print("  - if you meant USB: check the cable, and that you are in")
        print("    the dialout group (install_pet.sh adds you).")
        print("  - if wired to pins: TX/RX crossed, GND common, UART enabled")

    print("\n[VLM] (llama-server with Qwen3-VL-2B)")
    url = ((cfg.get("vlm") or {}).get("url", "http://127.0.0.1:8080")).rstrip("/")
    try:
        import requests

        requests.get(f"{url}/v1/models", timeout=5).raise_for_status()
        print(f"  OK: {url} responding")
    except Exception as exc:
        print(f"  WARN: {url} not responding ({exc});")
        print("  systemctl status wavego-pet-vlm  # first load takes ~1 min")

    print("\n[vision models]")
    vlm_cfg = cfg.get("vlm") or {}
    val_cfg = cfg.get("validator") or {}
    print(f"  VLM (proposer):  {vlm_cfg.get('provider', 'host')} "
          f"{vlm_cfg.get('url', '')} {vlm_cfg.get('model', '')}")
    print(f"  validator:       {val_cfg.get('provider', 'software')} "
          f"{val_cfg.get('url', '') or ''} {val_cfg.get('model', '') or ''}")
    try:
        validate_llm_config(vlm_cfg, val_cfg)
        print("  OK: validator model differs from VLM")
    except ConfigError as exc:
        print(f"  FAIL: {exc}")

    print("\n[camera port]")
    print(f"  configured: {(cfg.get('camera') or {}).get('port', 'dsi1')} "
          "(picamera2 opens the matching camera number)")

    print("\n[DSI LCD] (optional status display, DSI0 or DSI1)")
    dsi = detect_dsi_display()
    dcfg = cfg.get("display") or {}
    if dsi:
        print(f"  OK: {dsi} — configured {dcfg.get('width', 480)}x"
              f"{dcfg.get('height', 480)} on port {dcfg.get('port', 'n/a')}")
    else:
        print("  not attached (status UI disabled)")

    print("\n[local camera stack] (picamera2)")
    try:
        import picamera2  # noqa: F401

        print("  OK: picamera2 importable")
    except ImportError:
        print("  SKIP: picamera2 not importable (dev box?)")
    return 0


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
    parser.add_argument("--doctor", action="store_true",
                        help="check camera, ESP32 connection, VLM, DSI LCD and exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s [pet] %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.doctor:
        return run_doctor(cfg)

    try:
        validate_llm_config(cfg.get("vlm") or {}, cfg.get("validator") or {})
        validator = build_validator(cfg.get("validator") or {})
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    transport, vlm, frames, telemetry, persona = build_components(cfg, args.mock)
    display = build_display(cfg, args.mock)
    supervisor = MotionSupervisor(
        transport, telemetry=telemetry,
        logger=JsonlLogger((cfg.get("logging") or {}).get("jsonl_path")),
        rate_hz=(cfg.get("motion") or {}).get("rate_hz", 30.0))
    brain = PetBrain(
        supervisor, vlm, frames, persona,
        scene_every_s=args.scene_every
        or (cfg.get("brain") or {}).get("scene_every_s", 12.0),
        display=display, validator=validator,
        stats=None if args.mock else SystemStats())

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
        display.stop()
        supervisor.stop()
        if args.mock:
            log.info("wire transcript:\n%s",
                     "\n".join(getattr(transport, "wire_lines", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
