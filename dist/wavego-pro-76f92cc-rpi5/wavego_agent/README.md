# wavego_agent — semantic command layer for WAVEGO Pro

The AI-facing control foundation: a VLM or planner emits **semantic JSON**,
this package validates it, translates it to the **native WAVEGO protocol**,
and moves the robot through one **motion supervisor** that is the single
owner of the transport.

```
VLM / planner          "turn left 30°"          {"action":"turn","direction":"left","angle":30}
    │
    ▼
schema.py              strict Pydantic validation — unknown fields, bad ranges,
    │                  angle XOR duration: rejected here, never near the robot
    ▼
supervisor.py          bounded queue · preemption · emergency stop · telemetry
    │                  watchdog · 20–50 Hz tick loop · JSONL event log
    ▼
translate.py           semantic → native, every mapping verified against the
    │                  firmware (see table below)
    ▼
transport.py           MockTransport (records exact wire bytes) / SerialTransport
    │
    ▼
ESP32                  T:1 / T:110 / T:111 / T:112 / T:115 native JSON over UART
```

Relationship to the other stacks: `ugv_rpi/` is the operator web app and its
own control policies (`follow_ctrl.py`); `agent/` is the LLM task loop with
its dashboard. This package is the robot-motion foundation both of them can
sit on; nothing here imports either, so it runs on any dev box.

## Quick start (no hardware)

```python
from wavego_agent import (
    JsonlLogger, MotionSupervisor, MockTelemetry, MockTransport,
    parse_command,
)

transport = MockTransport()                 # records exact wire bytes
supervisor = MotionSupervisor(transport, telemetry=MockTelemetry(),
                              logger=JsonlLogger("session.jsonl"))
supervisor.submit({"action": "walk", "direction": "forward",
                   "speed": 0.4, "duration": 3})
while supervisor.busy:
    supervisor.tick()
print(transport.wire_lines)                 # exact JSON lines for the ESP32
```

Real hardware later: `MotionSupervisor(SerialTransport(), telemetry=..., ...)`.

## Command set

| Action  | Fields                                            | Native result                          |
| ------- | ------------------------------------------------- | -------------------------------------- |
| `walk`  | `direction`, `speed`, `duration`                  | `T:115` (cadence, if changed) + `T:111` |
| `run`   | `direction`, `speed`, `duration`                  | same, faster cadence                   |
| `turn`  | `direction`, exactly one of `angle`\|`duration`   | `T:111` gait; `angle` closes the loop on IMU yaw |
| `jump`  | —                                                 | `T:112` `{"func":3}`                   |
| `stand` | —                                                 | `T:110`                                |
| `sit`   | —                                                 | `T:112` `{"func":1}`                   |
| `stop`  | —                                                 | `T:111` `{"FB":0,"LR":0}` (failsafe's exact stop) |
| `approach` | `target`                                       | closed-loop vision behavior (bearing feed) |

Directions: `forward backward left right forward_left forward_right
backward_left backward_right` — the firmware gait table's 8 headings.

## Firmware-verified mapping (BodyCtrl.cpp / Config.h)

- Gait `T:111 {"FB":fb,"LR":lr}`, fb/lr ∈ {-1,0,1}: forward=(1,0),
  backward=(-1,0), left=(0,-1), right=(0,1), diagonals (1,-1) (1,1) (-1,-1)
  (-1,1) = ±30°/±120° headings.
- `speed` is normalized 0–1 and maps to gait cadence (`T:115` STEP_DELAY):
  0.5 reproduces firmware defaults (delay 5 ms); bounds [2, 8] are
  **provisional until verified on hardware** — recalibrate before real walks.
- FB/LR are integers in the firmware; analog speed is not possible at the
  protocol level, hence the cadence mapping.

## Testing

```bash
python -m pytest wavego_agent/tests -q
```

90 tests: schema rejection cases, every translation mapping, yaw wraparound
across the ±180° seam, preemption, emergency stop latching, queue bounds,
telemetry-watchdog safety stop, and the closed-loop turn/approach lifecycles
— all with an injected clock, no threads, no hardware.

## Before real hardware

1. Verify the cadence presets (STEP_DELAY bounds) and the `run` configuration
   on the physical robot; adjust `translate.py` anchors.
2. Swap `MockTelemetry` for a source fed by the app's T:1003/T:1001 feedback
   (yaw from IMU, voltage, moving flag).
3. Point `SerialTransport` at the UART and keep the single-owner rule: the
   failsafe daemon and the web app must not write while the supervisor runs.
4. Then wire the VLM output to `supervisor.submit()` — it can only emit
   valid, safe, semantic commands.
