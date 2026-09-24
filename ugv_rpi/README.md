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

- **Web UI**: `http://<robot-ip>:5000` — movement pad, StayLow / HandShake / Jump / STEADY buttons, RGB presets, JSON console, CV mode buttons, photo/video galleries
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
| `/getAudioFiles`, `/uploadAudio`, `/playAudio`, `/stop_audio` | GET/POST | audio library |

### Control bypass API (external applications)

For programs on or off the Raspberry Pi that need to drive the robot or read sensors without the web UI:

| Route | Method | Purpose |
|---|---|---|
| `/api/cmd` | POST | raw JSON passthrough to the ESP32 — body must be a command object with an integer `T` field (e.g. `{"T":111,"FB":1,"LR":0}`); returns `{"success":true,"sent":{...}}` |
| `/api/status` | GET | sensor/status snapshot: ESP32 battery voltage + last raw feedback, RPi CPU/temp/RAM/RSSI, video FPS + stream URLs, CV mode |
| `/video_feed` | GET | MJPEG camera stream (consume directly from any client) |
| `/offer` | POST | WebRTC signaling for the camera |

Example from another machine:

    curl -X POST http://<robot-ip>:5000/api/cmd -H "Content-Type: application/json" -d '{"T":112,"func":3}'
    curl http://<robot-ip>:5000/api/status

Note: the API is unauthenticated, like the rest of the app — intended for trusted LAN use.

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
| `cv` | `color_lower/upper`, `default_color`, `track_*_iterate`, `track_spd_rate`, `aimed_error`, `min_radius` | color tracking & CV tuning |
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
│                        #   face/color/motion/gesture/line modes, WebRTC frames,
│                        #   photo/video capture, timelapse, cv_light_mode RGB indicator
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
