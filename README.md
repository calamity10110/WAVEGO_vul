![GitHub top language](https://img.shields.io/github/languages/top/effectsmachine/WAVEGO_Pro) ![GitHub language count](https://img.shields.io/github/languages/count/effectsmachine/WAVEGO_Pro)
![GitHub code size in bytes](https://img.shields.io/github/languages/code-size/effectsmachine/WAVEGO_Pro)
![GitHub repo size](https://img.shields.io/github/repo-size/effectsmachine/WAVEGO_Pro) ![GitHub](https://img.shields.io/github/license/effectsmachine/WAVEGO_Pro) ![GitHub last commit](https://img.shields.io/github/last-commit/effectsmachine/WAVEGO_Pro)

# Waveshare Bionic Dog-Like Robot
Open Source for ESP32 And PI5/PI4B.

![](./README_footage/main.jpg)

## Basic Description
The WAVEGO is a 12-DOF bionic dog-like robot which features 2.3kg.cm large torque bus servos with feedback, reliable structure, and flexible motion, incorporating devices like front camera, 9-axes motion tracker, RGB indicator, etc., together with open source multi-platform Web application. It uses the ESP32 as sub controller for connecting rod inverse solving and gait generation, sharing calculating task for the host controller, an additional Raspberry Pi can be attached as the host controller for high-level decision operating.

The upper computer communicates with the lower computer (the robot's driver based on ESP32) by sending JSON commands via GPIO UART. The host controller, which employs a Raspberry Pi, handles AI vision and strategy planning, while the sub-controller, utilizing an ESP32, manages motion control and sensor data processing. This setup ensures efficient collaboration and enhanced performance.

## Features
- Real-time video based on WebRTC
- Cross-platform web application base on Flask
- Auto targeting (OpenCV)
- Object Recognition (OpenCV)
- Gesture Recognition (MediaPipe)
- Face detection (OpenCV & MediaPipe)
- Motion detection (OpenCV)
- Color Recognition (OpenCV)
- Multi-threaded CV processing
- Shortcut key control
- Photo taking
- Video Recording

## Contents
- [Robot Schematic](#robot-schematic)
- [Command Reference](#command-reference)
- [Install on the Raspberry Pi](#install-on-the-raspberry-pi)
- [Connect the Raspberry Pi to the ESP32 Mainboard](#connect-the-raspberry-pi-to-the-esp32-mainboard)
- [Sending Commands](#sending-commands)
- [Creating Custom Actions](#creating-custom-actions)
- [Camera Installation and Usage](#camera-installation-and-usage)

## Repository Layout
| Path | Content |
|---|---|
| `wavego_pro_platformio/` | ESP32 lower-computer firmware (PlatformIO, Arduino framework). Gait engine, IK, servo bus, OLED, RGB, missions, web UI — see [`wavego_pro_platformio/README.md`](./wavego_pro_platformio/README.md) |
| `ugv_rpi/` | Raspberry Pi upper-computer app (Flask + WebRTC + OpenCV/MediaPipe) — see [`ugv_rpi/README.md`](./ugv_rpi/README.md) |
| `wavego_pro_instruction_table.xlsx` / `.json` | Complete JSON command reference: 47 commands, feedback formats, unused/dead code audit |

## Robot Schematic
The system is a two-tier controller split by a JSON-over-UART contract:

```
                 Browser (phone / PC)
                   │ WebRTC video + websocket ctrl
                   ▼
    ┌──────────────────────────────┐
    │ Raspberry Pi (upper computer)│  Flask :5000, CV, strategy
    │  ugv_rpi app.py              │
    └──────────────┬───────────────┘
                   │ UART JSON @115200 (newline-terminated)
                   │ RPi GPIO14/TXD → mainboard RX0
                   │ RPi GPIO15/RXD ← mainboard TX0
                   ▼
    ┌──────────────────────────────┐
    │ ESP32 mainboard (lower)      │  gait IK, servo bus, sensors
    │  wavego_pro_platformio       │
    └──┬────────┬────────┬─────────┘
       │ UART1  │ I2C    │ GPIO
       ▼        ▼        ▼
  12x SCS    SSD1306  2x WS2812 RGB (GPIO26)
  bus servos OLED     buzzer (GPIO21)
  (RX18/TX19) ICM20948 IMU @0x68
              INA219 power @0x42
```

**ESP32 mainboard I/O map** (from firmware `Config.h` / `devs.h`):

| Function | Pin / Bus | Device |
|---|---|---|
| Bus servo UART | RX=18, TX=19 | 12× 2.3 kg·cm bus servos (SCServo/SCSCL protocol) |
| I2C | SDA=32, SCL=33 (400 kHz) | SSD1306 OLED 128×32 @0x3C, ICM20948 9-axis IMU @0x68, INA219 battery monitor @0x42 |
| RGB LEDs | GPIO26 | 2× WS2812 NeoPixel (id 0=left, 1=right) |
| Buzzer | GPIO21 | active buzzer |
| Assembly/debug mode | GPIO12 | jumper to 3V3 enables servo-level commands (T:102/103) |
| Host link | 2×5P expansion: RX0, TX0, G21, G15, G12, 3V3, 5V, GND | Raspberry Pi UART / USB Type-C |
| WiFi | built-in | AP `WAVEGO` / `12345678` @ `192.168.4.1`, web UI on :80 |

**Leg / joint layout** (joint index → servo ID, from `BodyCtrl.cpp`):

```
        LF_B[1]/52    forward    RF_B[7]/22
        LF_A[0]/53               RF_A[6]/23
          |                          |
        LF_H[2]/51                RF_H[8]/21

        LH_H[5]/43                RH_H[11]/33
          |                          |
        LH_A[3]/41                RH_A[9]/31
        LH_B[4]/42                RH_B[10]/32
```
jointID array (index 0..11): `{53, 52, 51, 41, 42, 43, 23, 22, 21, 31, 32, 33}` — servo positions 0..1023, zero/mid = 511. For `singleLegCtrl` commands legs are numbered 1=front-left, 2=hind-left, 3=front-right, 4=hind-right, with foot targets in mm (x=forward, y=height, z=lateral).

**Power**: 2× 18650 (2S, ~6.0–8.4 V) with protection circuit; INA219 monitors pack voltage (query with `{"T":207}`, telemetry `v` field of `T:1001`). Charging is possible while powered.

## Command Reference
Every command is one JSON object with a `"T"` type field. Full reference with parameters, handlers, examples, and feedback formats:

- **`wavego_pro_instruction_table.xlsx`** — 3 sheets: Instruction Table (47 commands), Feedback (telemetry), Unused Code audit
- **`wavego_pro_instruction_table.json`** — same data, machine-readable
- Waveshare wiki: [JSON Command Set](https://www.waveshare.com/wiki/WAVEGO_Pro#JSON_Command_Set) (documents the T:101–107 basics)

## Install on the Raspberry Pi

**Option A — prebuilt image (recommended for first use):** flash the official WAVEGO image with Raspberry Pi Imager ([download](https://drive.google.com/file/d/1fQcucuv19uY6qhOVKPc3t1JqV-zLIHE-/view), tutorial: [Product Assembly and RPi Environment Configuration](https://www.waveshare.com/wiki/WAVEGO_Pro-Product_Assembly_and_Raspberry_Pi_Environment_Configuration)). Everything below is already installed and configured.

**Option B — from source on a clean Raspberry Pi OS (Bookworm):**

### Download the repo from github

You can clone this repository from Waveshare's GitHub to your local machine.

    git clone https://github.com/waveshareteam/WAVEGO_Pro.git
    
### Grant execution permission to the installation script
    cd WAVEGO_Pro/ugv_rpi/
    sudo chmod +x setup.sh
    sudo chmod +x autorun.sh
### Install app (it'll take a while before finish)
    sudo ./setup.sh
### Autorun setup
    ./autorun.sh
### AccessPopup installation
    cd AccessPopup
    sudo chmod +x installconfig.sh
    sudo ./installconfig.sh
    *Input 1: Install AccessPopup
    *Press any key to exit
    *Input 9: Exit installconfig.sh
### Reboot Device
    sudo reboot

After powering on the robot, the Raspberry Pi will automatically establish a hotspot, and the LED screen will display a series of system initialization messages:  

![](./ugv_rpi/media/RaspRover-LED-screen.png)
- The first line `E` displays the IP address of the Ethernet port, which allows remote access to the Raspberry Pi. If it shows No Ethernet, it indicates that the Raspberry Pi is not connected to an Ethernet cable.
- The second line `W` indicates the robot's wireless mode. In Access Point (AP) mode, the robot automatically sets up a hotspot with the default IP address `192.168.50.5`. In Station (STA) mode, the Raspberry Pi connects to a known WiFi network and displays the IP address for remote access.
- The third line `F/J` specifies the Ethernet port numbers. Port `5000` provides access to the robot control Web UI, while port `8888` grants access to the JupyterLab interface.
- The fourth line `STA` indicates that the WiFi is in Station (STA) mode. The time value represents the duration of robot usage. The dBm value indicates the signal strength RSSI in STA mode.  


You can access the robot web app using a mobile phone or PC. Simply open your browser and enter `[IP]:5000` (for example, `192.168.10.50:5000`) in the URL bar to control the robot.  

If the robot is not connected to a known WiFi network, it will automatically set up a hotspot named "`AccessPopup`" with the password `1234567890`. You can then use a mobile phone or PC to connect to this hotspot. Once connected, open your browser and enter `192.168.50.5:5000` in the URL bar to control the robot.  


### Troubleshooting: v4l2 errors
If the program fails to run and encounters errors related to v4l2.py during runtime, you need to delete v4l2.py from both the Python virtual environment and the user environment. This will allow the program to automatically use the system-wide v4l2.py.  

    cd WAVEGO_Pro/ugv_rpi/  
    sudo rm ugv-env/lib/python3.11/site-packages/v4l2.py  
    sudo rm /home/[your_user_name]/.local/lib/python3.11/site-packages/v4l2.py  

Now you can restart the main program app.py.

## Connect the Raspberry Pi to the ESP32 Mainboard
The RPi talks to the ESP32 lower computer over a 3-wire UART link using the mainboard's 2×5P expansion port:

| Mainboard expansion pin | Raspberry Pi GPIO |
|---|---|
| RX0 | GPIO14 / TXD (pin 8) |
| TX0 | GPIO15 / RXD (pin 10) |
| GND | GND (pin 6) |
| 5V (optional) | 5V — only if powering the RPi from the robot |

Serial parameters: **115200 baud, 8N1**, one JSON object per line (newline-terminated). The app auto-detects the board: Pi 5 uses `/dev/ttyAMA0`, Pi 4B and earlier use `/dev/serial0` (`app.py` checks `/proc/cpuinfo`).

**Enable the UART on the Raspberry Pi:**

    sudo raspi-config
    # Interface Options -> Serial Port
    #   "Would you like a login shell over serial?" -> No
    #   "Would you like the serial port hardware enabled?" -> Yes
    sudo reboot

Verify after reboot: `ls -l /dev/serial0` should point to `ttyS0` (Pi 4) or `ttyAMA0` (Pi 5), and `dmesg | grep tty` should show the UART. With the mainboard connected and powered, a quick end-to-end test is opening the port and typing `{"T":207}` — the board should answer `{"T":-207,"voltage":7.xx}`:

    sudo apt install picocom
    picocom -b 115200 /dev/serial0     # /dev/ttyAMA0 on Pi 5
    # type: {"T":207}  then Enter; exit with Ctrl+A, Ctrl+X

Notes:
- **Flashing the ESP32**: disconnect the RPi UART first (or the upload fails with "Failed to connect to ESP32"). Alternatively use the Type-C port from a PC.
- The ESP32 firmware also accepts commands over USB Type-C (same 115200 JSON protocol) and over its own WiFi AP (`WAVEGO` / `12345678`, web UI at `192.168.4.1`).
- The expansion port also exposes G21, G15, G12, 3V3 for assembly-mode control and extensions.

## Sending Commands
All 47 commands are listed in [wavego_pro_instruction_table.xlsx](./wavego_pro_instruction_table.xlsx) with parameters and examples. Quick recipes:

**1. Web UI console (no code):** open the robot's web app (`[IP]:5000`), use the FEEDBACK INFORMATION input box, e.g. `{"T":111,"FB":1,"LR":0}` to walk forward. The ESP32's own web UI (`192.168.4.1`) has the same console.

**2. Serial from a PC (Python):**

    import serial, threading
    ser = serial.Serial('COM20', 115200)   # or /dev/ttyUSB0
    ser.write(b'{"T":111,"FB":1,"LR":0}\n')
    print(ser.readline())                   # telemetry / feedback

**3. HTTP over the ESP32 WiFi:**

    http://192.168.4.1/js?json={"T":106}   # returns {"T":-106,"fb":[...]}

**4. From Raspberry Pi code** (the app exposes the BaseController):

    from base_ctrl import BaseController
    base = BaseController('/dev/serial0', 115200)   # '/dev/ttyAMA0' on Pi 5
    base.base_speed_ctrl(0.5, 0.5)   # {"T":1,...} drive
    base.gimbal_ctrl(0, 0, 200, 10)  # {"T":133,...} body pose
    base.rgb_light(0, 255, 0, 64)    # {"T":201,...} RGB LED
    base.base_oled(0, "hello")       # {"T":202,...} OLED line

Core motion commands: `{"T":111,"FB":-1..1,"LR":-1..1}` vector gait, `{"T":112,"func":1..5}` StayLow/HandShake/Jump/steady on/off, `{"T":110}` stand, `{"T":114,"h":95}` height, `{"T":133,"X":..,"Y":..}` body pose (yaw/pitch).

## Creating Custom Actions
Two ways, no recompile vs full control:

**A. Mission scripts (JSON only, stored in ESP32 LittleFS):** a mission is a named sequence of command lines executed step by step.

    {"T":301,"name":"mydance","intro":"custom action demo"}        # create
    {"T":303,"name":"mydance","json":"{\"T\":114,\"h\":80}"}       # append step
    {"T":303,"name":"mydance","json":"{\"T\":112,\"func\":3}"}     # append step
    {"T":308,"name":"mydance","interval":1000,"loop":1}            # run

Edit steps with T:304/T:305/T:306, list with T:302, delete with T:309. The `boot` mission is executed at every power-on (holds WiFi config and zero calibration — edit with care).

**B. Firmware action (C++, full IK control):** add a method to `BodyCtrl` in `wavego_pro_platformio/WAVEGO_Pro_PlatformIO/src/BodyCtrl.cpp/.h` and call it from the `T:112` dispatcher in `main.cpp`. The existing actions are the reference pattern — a body-height sweep with smooth interpolation:

    void BodyCtrl::functionActionA(){
      for(float i = 0; i <= 1; i += 0.02){
        standUp(besselCtrl(WALK_HEIGHT, WALK_HEIGHT_MIN, i)); // easing curve 95 -> 75 mm
        allJointAngle(GoalAngle);      // solve all legs from the buffer, then move together
        delay(6);
      }
    }

Building blocks (all in `BodyCtrl`):
- `standUp(height_mm)` — set target body height (75..110); IK is written to `GoalAngle[]`, servos do not move yet
- `singleLegCtrl(leg1to4, x, y, z)` — foot target in mm per leg (x forward, y height, z lateral)
- `pitchYawRoll(pitch, yaw, roll)` — body posture
- `besselCtrl(start, end, i)` — cosine easing between two values as `i` goes 0→1 for natural motion (use `linearCtrl` for constant speed)
- `allJointAngle(GoalAngle)` — apply all computed targets so every servo starts moving at the same instant

Wire it into `main.cpp`:

    case CMD_BASIC_FUNC:
        if (jsonCmdInput["func"] == 1) { ... }
        else if (jsonCmdInput["func"] == 6) { bodyCtrl.functionActionA(); }  // new slot

Then trigger it with `{"T":112,"func":6}`. Build and flash with PlatformIO (`pio run -t upload` from `wavego_pro_platformio/WAVEGO_Pro_PlatformIO/`, mainboard connected via Type-C, RPi UART disconnected). Background reading: [WAVEGO Custom Action Development Tutorial](https://www.waveshare.com/wiki/WAVEGO_Custom_Action_Development_Tutorial) (uses the older WAVEGO naming `GoalPosAll()`/`GoalPWM[]`; in this Pro firmware the equivalents are `allJointAngle(GoalAngle)`/`GoalAngle[]`).

## Camera Installation and Usage
The RPi app auto-detects cameras at startup in this order: **USB → CSI → OAK (depthai)**.

**CSI camera (recommended, kit includes ultra-wide lens):**
1. Power off. Connect the camera to the Pi's CAM/DISP CSI port with the ribbon cable — Pi 5 uses the 22-pin mini FPC (smaller cable, metal contacts toward the board); Pi 4B uses the 15-pin ribbon (contacts toward the Ethernet side on the official cable orientation).
2. Boot and verify the module is detected:

       cat /proc/device-tree/model        # confirm board
       rpicam-hello -t 5000               # Pi 5 / Bookworm (or: libcamera-hello -t 5000)

   If the preview shows, the camera works. On older OS ensure `camera_auto_detect=1` in `/boot/firmware/config.txt` (default on current images).

   **OV5647 camera module** (Camera v1 family) is often not auto-detected, especially on the DSI-side/CAM0 connector. If `rpicam-hello` reports no camera, add the explicit overlay to `/boot/firmware/config.txt` and reboot:

       #DSI/cam 0 Use for ov5647 cam
       dtoverlay=ov5647,cam0

   Verify with `rpicam-hello --camera 0 -t 5000` after reboot, and check `dmesg | grep -i ov5647` shows the sensor registering on the `csi0`/`cam0` pipeline.
3. The app uses `picamera2` (already in `requirements.txt`); no extra driver install is needed.

**USB camera:** plug in and boot — the app opens it with OpenCV (`/dev/video0`, 640×480 default). Nothing to configure.

**Resolution/quality tuning:** `ugv_rpi/config.yaml` → `video: default_res_w / default_res_h / default_quality`.

**Usage:** open the web app → live WebRTC video (fallback MJPEG `/video_feed`). CV functions run on the stream: face/object/gesture/motion detection and color tracking (buttons in the web UI) — when tracking drives the robot, pose output goes through T:133. Photos and videos are captured to the Pi and browsable in the gallery pages. If video fails with v4l2 errors, see the troubleshooting note above.

# License
WAVEGO_Pro for the Raspberry Pi: an open source robotics platform for the Raspberry Pi.
Copyright (C) 2024 [Waveshare](https://www.waveshare.com/)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/gpl-3.0.txt>.
