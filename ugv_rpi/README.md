![GitHub top language](https://img.shields.io/github/languages/top/waveshareteam/WAVEGO_Pro) ![GitHub language count](https://img.shields.io/github/languages/count/waveshareteam/WAVEGO_Pro) ![GitHub](https://img.shields.io/github/license/waveshareteam/WAVEGO_Pro) ![GitHub last commit](https://img.shields.io/github/last-commit/waveshareteam/WAVEGO_Pro)

# WAVEGO Pro — Raspberry Pi Upper Computer (`ugv_rpi`)

Flask + WebRTC + OpenCV/MediaPipe application that runs on the Raspberry Pi (Pi 4B / Pi 5) mounted on the WAVEGO Pro quadruped. It streams video, runs AI vision modes, serves the browser control UI, and drives the ESP32 mainboard over UART JSON.

The ESP32 lower computer lives in [`../wavego_pro_platformio/`](../wavego_pro_platformio/README.md). The full command protocol reference is [`../wavego_pro_instruction_table.xlsx`](../wavego_pro_instruction_table.xlsx) / `.json`.

## Usage

### Install (from a clean Raspberry Pi OS Bookworm)

    cd WAVEGO_Pro/ugv_rpi/
    sudo chmod +x setup.sh autorun.sh
    sudo ./setup.sh          # venv + requirements + services (takes a while)
    ./autorun.sh             # enable boot autorun
    cd AccessPopup && sudo chmod +x installconfig.sh && sudo ./installconfig.sh
    #   -> 1 (install), any key, 9 (exit)
    sudo reboot

Or flash the prebuilt WAVEGO image — see the [root README](../README.md) install section.

### Run / stop

The app auto-starts on boot (`app.py`). Manual control:

    cd ugv_rpi && source ugv-env/bin/activate
    python app.py            # Flask-SocketIO on 0.0.0.0:5000

### Access

- **Web UI**: `http://<robot-ip>:5000` — shared tab bar across all pages: Control (movement pad, CV mode buttons incl. FOLLOW, JSON console), Dashboard (telemetry + d-pad), **Telemetry (joints, sensors, host/client errors, VLM session log)**, Joystick (dual-stick), Gait (ESP32 live tuning), Settings (camera/movement/identity), Photos, Videos
- **Audio upload/playback removed** — the unused pygame/pyttsx3 stack was dead code and its endpoints returned fake success
- **JupyterLab tutorials**: `http://<robot-ip>:8888`
- **Hotspot fallback**: if no known WiFi is found, AccessPopup opens `AccessPopup` / `1234567890` with the UI at `192.168.50.5:5000`
- The robot IP is shown on the OLED (line `W:`) and announced on the video overlay at boot

### Camera

Detection order at startup: **USB → CSI (Picamera2) → OAK (depthai)**. CSI ribbon goes to the CAM/DISP port (22-pin on Pi 5, 15-pin on Pi 4B). Test with `rpicam-hello -t 5000`. For the OV5647 module on the DSI/CAM0 connector, add to `/boot/firmware/config.txt`:

    #DSI/cam 0 Use for ov5647 cam
    dtoverlay=ov5647,cam0

Resolution/quality: `config.yaml` → `video:`. If the app crashes with v4l2 errors, delete the duplicated v4l2.py from the venv and user site-packages (see [root README](../README.md) troubleshooting).

## API

### Serial link to the ESP32 (what this app actually sends)

`app.py` opens the UART at **115200** — `/dev/ttyAMA0` on Pi 5, `/dev/serial0` on Pi 4B (auto-detected from `/proc/cpuinfo`). One JSON object per line.

Live emitters (all implemented by the WAVEGO Pro firmware):

| Method (`base_ctrl.BaseController`) | JSON | Purpose |
|---|---|---|
| `base_speed_ctrl(L, R)` | `{"T":1,"L":..,"R":..}` | drive / heartbeat (keyboard, web pad) |
| `gimbal_ctrl(x, y, spd, acc)` | `{"T":133,"X":..,"Y":..,"SPD":..,"ACC":..}` | body pose (also used by CV tracking) |
| `rgb_light(id, r, g, b)` | `{"T":201,"set":[..]}` | RGB LEDs (boot breath, face-detect indicator) |
| `base_oled(line, text)` | `{"T":202,"line":..,"text":..,"update":1}` | OLED status lines (IP, uptime) |
| `base_json_ctrl(obj)` / `send_command(obj)` | any | raw passthrough |

Everything else the firmware understands (47 commands — missions, gait tuning, WiFi, ESP-NOW…) can be sent through the raw passthrough or the web JSON console.

### HTTP routes (Flask)

| Route | Method | Purpose |
|---|---|---|
| `/` , `/<file>` | GET | web UI + static assets |
| `/config` | GET | serves `config.yaml` to the UI JS |
| `/offer` | POST | WebRTC signaling (video) |
| `/video_feed` | GET | MJPEG fallback stream |
| `/send_command` | POST | send a cmdline string to `cmdline_ctrl` |
| `/get_photo_names`, `/delete_photo`, `/get_video_names`, `/delete_video`, `/videos/<f>` | GET/POST | media galleries |

### Control bypass API (external applications)

For programs on or off the Raspberry Pi that need to drive the robot or read sensors without the web UI:

| Route | Method | Purpose |
|---|---|---|
| `/api/cmd` | POST | raw JSON passthrough to the ESP32 — body must be a command object with an integer `T` field (e.g. `{"T":111,"FB":1,"LR":0}`); returns `{"success":true,"sent":{...}}`. Optional `?sync_ms=500` short-polls (max 3000 ms) for the negative-`T` reply of query commands (T:106 positions, T:207 battery) and returns it as `response` |
| `/api/cv` | GET/POST | switch/query CV modes + motion lock by name — `GET` returns `{"mode":"person","mode_code":10310,"motion_lock":false}`; `POST {"mode":"..."}` and/or `{"motion_lock":bool}`. Valid mode names: `none, motion, face, objects, color, hand, auto, mp_face, mp_pose, person` |
| `/api/vlm` | GET/POST | proxy to the VLM goal service (`vlm_ctrl`, port 5001) — `GET` returns session status; `POST {"goal":"..."}` or `{"goals":[...]}` (sequential = autonomous mode); 503 when the service is not running |
| `/api/vlm/stop` | POST | stop robot motion + end the current VLM session |
| `/api/config` | GET/POST | whitelisted runtime settings — `GET` returns flat `key: value` (video res/quality, movement speeds/rates, robot name); `POST` a partial patch (e.g. `{"video.default_quality": 55}`) to validate, apply, and persist to `config.yaml`. Unknown keys / out-of-range values are rejected with 400 |
| `/api/telemetry` | GET | realtime snapshot: polled joint positions (T:106 @1 Hz, `joints.fb`[12] + timestamp), battery, last raw ESP32 feedback, RPi cpu/temp/ram/rssi, video FPS, CV mode, and the last 50 host errors (logging ≥WARNING + Flask exceptions, bounded ring). Consumed by the Telemetry tab |
| `/api/status` | GET | sensor/status snapshot: ESP32 battery voltage + last raw feedback, RPi CPU/temp/RAM/RSSI, video FPS + stream URLs, CV mode |
| `/video_feed` | GET | MJPEG camera stream (consume directly from any client) |
| `/offer` | POST | WebRTC signaling for the camera |

Example from another machine:

    curl -X POST http://<robot-ip>:5000/api/cmd -H "Content-Type: application/json" -d '{"T":112,"func":3}'
    curl -X POST "http://<robot-ip>:5000/api/cmd?sync_ms=500" -H "Content-Type: application/json" -d '{"T":207}'
    # -> {"success":true,"sent":{"T":207},"response":{"T":-207,"voltage":7.82}}
    curl http://<robot-ip>:5000/api/status

Note: the API is unauthenticated, like the rest of the app — intended for trusted LAN use.

### Person following (FOLLOW mode)

The FOLLOW button in the web UI (or `POST /api/cv {"mode":"person"}`) starts a person-follow mode built on the bundled MobileNet-SSD detector (`models/mobilenet_iter_73000.caffemodel`, person class). Control policy (see `follow_ctrl.py`, unit-tested in `tests/`):

- target off-center beyond `person_deadzone` (fraction of frame width) → turn toward it (`{"T":111,"FB":0,"LR":±1}`)
- centered and bounding-box height < `person_far_ratio` → walk forward (`FB=1`)
- centered and box height > `person_near_ratio` → stop (too close); values between the two thresholds hold the previous move/stop decision (hysteresis band, prevents boundary oscillation)
- no person for `person_lost_frames` consecutive frames → stop

Commands are discrete `T:111` vectors per the firmware contract and are sent only on decision change. A frame-tick watchdog stops the robot if the video pipeline stalls for >2 s while the mode is active; leaving the mode (switching to any other mode, or pressing LOCK) sends an immediate stop. Movement is gated by the existing motion-lock like other tracking modes. Tuning: `config.yaml` → `cv: person_*`.

### Known limitation: motion commands outlive the controlling process

`T:111` gait commands are **level-based** — the ESP32 firmware repeats the last command until the next one arrives. There is no command timeout in the firmware, so if the process that issued a movement dies before sending a stop, **the robot keeps walking**. Concretely:

- `app.py` crashing, being killed, or the Pi rebooting / losing power mid-walk while the mainboard stays powered — this affects person-follow, the web movement pad, and keyboard control alike (pre-existing exposure, not introduced by the follow mode)
- the voice assistant dying inside the `move_timeout_s` window — the auto-stop is enforced by the voice process itself

Safeguards that do **not** cover whole-process death: the follow-mode watchdog (>2 s video stall), stop-on-mode-exit, LOCK, voice `move_timeout_s`. Once the controlling process is gone, the web UI cannot help either — it is served by the same dead process — and the **only stop is physical: the power switch or battery disconnect**.

Operational recommendations:

1. **Suspension first**: for any new build or parameter change, put the robot on a stand (belly supported, feet off the ground) and verify mode/state behavior with zero locomotion risk.
2. **Leash on first ground tests**: attach a drag line to the chassis before enabling FOLLOW or voice movement on the ground.
3. **Rehearse the failure**: know where the power switch is before the first autonomous walk. On a leash, test it deliberately — start following, `pkill -f app.py`, observe that the robot keeps moving, cut power. That is expected behavior, not a bug to chase.
4. The definitive fix is firmware-side — a gait auto-timeout in `wavego_pro_platformio` (stop when no `T:111`/`T:1` arrives within N ms). Future work; everything above is mitigation until it exists.

## Voice assistant (optional)

`voice/voice_assistant.py` is a persistent voice-command process: USB mic (16 kHz mono) → energy VAD with endpointing → Moonshine ONNX transcription → phrase grammar in `voice/voice_config.yaml` → robot via `/api/cmd` and `/api/cv`. The model is loaded once at startup — per-utterance cost is inference only (spawning a fresh Python+ONNX process per command costs ~3.2 s, which is what this design avoids). Gait commands are level-based, so every voice-triggered movement auto-stops after `move_timeout_s` (default 3 s) unless a follow-up command arrives. If the voice process itself dies inside that window, the gait persists — see [*Known limitation*](#known-limitation-motion-commands-outlive-the-controlling-process) above.

Install on the robot (from `ugv_rpi/`):

    ./install_voice.sh                       # deps + systemd service + speaker volume fix
    ./install_voice.sh --check-autostart     # scan for duplicate/legacy autostart entries
    ./install_voice.sh --clean-listen        # remove legacy listen.py cron lines
    ./install_voice.sh --uninstall
    journalctl -u wavego-voice -f            # live logs

Hardware mapping (adjust `voice/voice_config.yaml` to match `aplay -l` / `arecord -l`): mic = C-Media USB device (found by `mic.name_hint`, card 1 in the reference setup), speaker = Jieli card 0; feedback beeps play through `aplay -D plughw:0,0` so the speaker device is never held open. The installer also raises the speaker PCM to 85 % (`SPEAKER_CARD`/`SPEAKER_VOLUME` env overrides) and runs `alsactl store`.

Default grammar: *stop following / follow me* (CV mode switch), *find the ball / vision task / explore* (VLM goal sessions), *stop task / cancel task*, *stop / halt*, *sit down / stand up / jump*, *forward / back / turn left / turn right*. Phrases map to raw T-commands, CV mode switches, or VLM goals; first match wins (ordered token matching, so filler words are tolerated), so keep specific phrases above generic ones in the config.

## VLM goal control (optional)

`vlm_ctrl/` is a goal-session runtime driven by an onboard vision-language decision model (Intern-Decision-0.8B, fine-tuned from Qwen3.5-0.8B): camera frame + goal in → one forward pass per cycle → action label + goal status with calibrated probabilities → validated, verified, then dispatched as whitelist commands through `/api/cmd`. Runs as a separate systemd service (`python -m vlm_ctrl.service`, port 5001; `app.py` proxies `/api/vlm`) so torch never loads into the main app.

Verification is software-first — the model never emits raw T-commands (labels map to a 9-command whitelist), decisions pass a JSON schema, a text-coherence heuristic, calibrated confidence thresholds, and temporal persistence: a *reached/impossible* claim is only trusted after it appears with high confidence on two different frames. An optional online verifier (any OpenAI-compatible endpoint, `online_verifier:` in `vlm_ctrl/config.yaml`) audits high-stakes moments and degrades silently to software checks when unreachable. Failure ladder per decision: retry with error feedback, escalate, and after 3 strikes send STOP and end the session. Every session ends in a stop unless the final action is a safe pose; movement dispatches re-arm a move-timeout.

Install on the robot (from `ugv_rpi/`):

    # 1. download the checkpoint, then point fast_engine.checkpoint_dir at it:
    #    https://huggingface.co/internlm/Intern-Decision-0.8B
    ./install_vlm.sh                # deps + systemd service
    ./install_vlm.sh --benchmark    # latency gate: mean cycle must be <= 2.5 s
    ./install_vlm.sh --check
    journalctl -u wavego-vlm -f

If the benchmark fails the gate, set `capture.resize_px: 224` in `vlm_ctrl/config.yaml` and re-run. Triggers: dashboard **AI Goal** box, `POST /api/vlm`, or the voice phrases above. RAM: the engine needs ~3 GB resident alongside the main app (~1.5 GB) on the 8 GB Pi — avoid running VLM sessions and FOLLOW mode simultaneously. The [known limitation](#known-limitation-motion-commands-outlive-the-controlling-process) applies to VLM-driven movement too.

### WebSockets (Flask-SocketIO)

- `/ctrl` — inbound `{"A": <code>, ...}`; `code` values come from `config.yaml` `code:` section (movement keys, CV modes, photo/video, LED modes, motion lock). Unknown codes are ignored.
- `/json` — inbound JSON forwarded verbatim to the ESP32 (the web JSON console uses this).
- `update` (emit) — periodic telemetry to the UI: CPU/RAM/temp, RSSI, battery voltage (`v` from the `T:1001` heartbeat), pan/tilt estimates, video FPS.

### Feedback from the ESP32

`BaseController.feedback_data()` parses serial lines: battery voltage arrives in the `T:1001` telemetry every 5 s; `{"T":-106,...}` / `{"T":-207,...}` answer position/voltage queries. See the Feedback sheet of the instruction table.

## Settings

All runtime settings live in **`config.yaml`**:

| Section | Keys | Meaning |
|---|---|---|
| `base_config` | `robot_name`, `main_type` (2), `module_type` (0), `use_lidar`, `extra_sensor` | identity & hardware options |
| `args_config` | `max_speed`, `slow_speed`, `min/mid/max_rate` | speed scaling for keyboard/web pad |
| `video` | `default_res_w/h` (640×480), `default_quality` | camera output |
| `cv` | `color_lower/upper`, `default_color`, `track_*_iterate`, `track_spd_rate`, `aimed_error`, `min_radius`, `person_*` (follow mode: confidence, deadzone, far/near ratio, lost frames, class id) | color tracking, person following & CV tuning |
| `audio_config` | `audio_output`, `default_volume`, `min_time_bewteen_play` | audio behavior |
| `code` / `fb` | numeric UI command/feedback codes | contract with `control.js` |
| `cmd_config` | `cmd_movition_ctrl` (1), `cmd_gimbal_ctrl` (133), `cmd_gimbal_steady` (137), `cmd_arm_ctrl_ui` (144), `cmd_pwm_ctrl` (11) | T-code overrides |
| `sbc_config` | `feedback_interval`, `disabled_http_log` | app behavior |

Hardware settings (UART enable, camera overlays incl. `dtoverlay=ov5647,cam0`) live in `/boot/firmware/config.txt` — see root README.

## Codemap

```
ugv_rpi/
├── app.py               # entry point: Flask+SocketIO app, BaseController init (Pi4/Pi5
│                        #   serial device autodetect), routes, websocket handlers,
│                        #   cmd_actions dispatch table, feedback/OLED update loops
├── base_ctrl.py         # BaseController: threaded serial JSON queue, ReadLine parser,
│                        #   emitters (T:1/133/201/202), breath_light demo
├── cv_ctrl.py           # CameraFlinger/CV engine: USB→CSI→OAK detect, Picamera2/OpenCV,
│                        #   face/color/motion/gesture/line modes, person-follow mode,
│                        #   WebRTC frames, photo/video capture, timelapse, cv_light_mode
├── follow_ctrl.py       # person-follow control policy: dead-zone, hysteresis band,
│                        #   lost-frame counter → discrete T:111 FB/LR decisions (pure logic)
├── tests/               # unit tests for follow_ctrl (run: python -m pytest tests/)
├── voice/               # persistent voice assistant: Moonshine ONNX + energy VAD +
│                        #   voice_config.yaml grammar → /api/cmd + /api/cv + /api/vlm
├── install_voice.sh     # voice service installer: systemd unit, speaker volume fix,
│                        #   autostart duplicate scanner (--check-autostart/--clean-listen)
├── vlm_ctrl/            # VLM goal control: Intern-Decision engine, goal-session state
│                        #   machine (3-strike ladder, claim verification, stall detection),
│                        #   software+optional-online verification, benchmark harness
│                        #   (python -m vlm_ctrl.benchmark, gate 2.5 s/cycle)
├── install_vlm.sh       # VLM service installer: systemd unit, deps, checkpoint check
├── audio_ctrl.py        # TTS / audio playback helpers
├── os_info.py           # system info (si): CPU/RAM/temp, IP addresses, RSSI, folders
├── config.yaml          # all runtime settings (see Settings)
├── requirements.txt     # pinned deps (Flask, aiortc, opencv, mediapipe, picamera2, torch…)
├── setup.sh / autorun.sh / start_jupyter.sh
├── AccessPopup/         # WiFi hotspot fallback manager
├── templates/           # web UI: index.html + control.js (socket cmds, keyboard, gamepad)
│                        #   photo.html/video.html galleries, style.css, vendored libs
├── media/  sounds/  models/   # UI assets, audio files, CV models
├── tutorial_en/ tutorial_cn/  # Jupyter tutorials (generic Waveshare UGV command set —
│                               # NOT all commands apply to WAVEGO Pro; see instruction table)
└── 99-dai.rules  asound.conf  # device/audio udev config
```

Threads at runtime: `process_commands` (serial writer), `generate_frames`/WebRTC (video), `update_data_loop` (telemetry + OLED), `base_data_loop` (serial reader / lidar / sensors).

## License

GPL-3.0 — see [root README](../README.md).
