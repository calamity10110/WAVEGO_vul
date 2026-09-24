# WAVEGO Pro — ESP32 Lower Computer Firmware (`wavego_pro_platformio`)

PlatformIO (Arduino framework) firmware for the ESP32 mainboard of the WAVEGO Pro quadruped. Owns everything real-time: 12-servo bus control, five-bar leg inverse kinematics, triangular gait engine, IMU self-balancing, OLED, RGB LEDs, buzzer, battery monitoring, LittleFS mission scripts, WiFi web UI, and ESP-NOW.

The Raspberry Pi upper computer lives in [`../ugv_rpi/`](../ugv_rpi/README.md). Full 47-command protocol reference: [`../wavego_pro_instruction_table.xlsx`](../wavego_pro_instruction_table.xlsx) / `.json`.

## Usage

### Build & flash

    cd wavego_pro_platformio/WAVEGO_Pro_PlatformIO
    pio run -t upload && pio device monitor

Requirements: [PlatformIO](https://platformio.org/) with the `espressif32` platform (resolved automatically). Libraries from `platformio.ini`: SCServo, ArduinoJson 7, Adafruit NeoPixel / SSD1306 / GFX, ICM20948_WE, INA219_WE, SimpleKalmanFilter.

**Disconnect the Raspberry Pi UART before flashing** (or hold BOOT during upload if it still times out) — the RPi shares the serial link.

### First boot behavior

1. OLED shows `WAVESHARE Robotics`, IMU/INA219/RGB/servos initialize, buzzer chirps.
2. A `boot` mission is created in LittleFS on first run and executed at every power-on: sets WiFi AP (`WAVEGO` / `12345678`) and stores the servo zero calibration (`{"T":105,...}` appended when you run T:107).
3. Web server starts on port 80 — connect to the `WAVEGO` AP and open `192.168.4.1`, or join a configured STA network and use the IP shown on the OLED.
4. Jumper **GPIO12 → 3V3** enters assembly/debug mode (RGB turns orange, joints center): enables raw servo commands T:102/103 for calibration.

### Web UI

Direction pad (gait), StayLow / HandShake / Jump / STEADY START/STOP buttons, and a JSON console (`FEEDBACK INFORMATION` box) — all traffic goes to `GET /js?json=<cmd>`.

## API

Single JSON dispatcher (`jsonCmdReceiveHandler` in `src/main.cpp`), fed from three transports:

| Transport | Details |
|---|---|
| UART | 115200 baud, one JSON object per line (newline-terminated) — Raspberry Pi link or USB Type-C |
| HTTP | `GET /js?json=<url-encoded JSON>` on port 80; response body = feedback JSON |
| ESP-NOW | peer payloads routed into the same handler when initialized (T:410/411) |

Command groups (all 47 with parameters in the [instruction table](../wavego_pro_instruction_table.xlsx)):

| Range | Group | Highlights |
|---|---|---|
| 1, 133 | drive compat / body pose | `{"T":1,"L":..,"R":..}`, `{"T":133,"X":..,"Y":..}` |
| 101-107 | servo calibration | middle / torque / single servo / zero get-set (102-103 assembly mode only) |
| 108-116 | motion | joint angle/rad, stand, vector move `T:111`, functions `T:112` (stayLow/handshake/jump/steady), leg ctrl, height, interpolation & gait tuning |
| 201-207 | peripherals | RGB `T:201`, OLED 202-205, buzzer 206, battery 207 |
| 300-399 | missions (LittleFS) | create/append/insert/replace/delete/run steps, format flash |
| 400-403 | WiFi | mode set (persisted to `boot`), info, AP/STA IP |
| 410-414 | ESP-NOW | init, mode, MAC get/send/add-peer |
| 600-601 | system | reboot, clear NVS |

Feedback (ESP32 → host): `{"T":-106,"fb":[12]}` positions, `{"T":-207,"voltage":V}` battery, positive echoes for T:105/400, and unsolicited telemetry `{"T":1001,...,"v":<V>,...}` every 5 s. T:104/300/302/401-403/412 reply as plain serial text.

Known pitfalls: T:300/303-306 have **different meanings** than in Waveshare's generic UGV stack (mission files vs ESP-NOW peer ops) — don't send UGV-style `send`/`T:300` traffic here. RGB channel order is r/g-swapped at the strip (`RGBLight.cpp` passes `Color(g,r,b)`).

## Settings

### Compile-time — `src/Config.h`

| Setting | Default | Meaning |
|---|---|---|
| `RGB_PIN` / `RGB_NUM` | 26 / 2 | WS2812 data pin, LED count |
| `BUS_SERVO_RX` / `BUS_SERVO_TX` | 18 / 19 | servo bus UART |
| `I2C_SDA` / `I2C_SCL` | 32 / 33 | I²C bus (OLED 0x3C, ICM20948 0x68, INA219 0x42) |
| `BUZZER_PIN` | 21 | active buzzer |
| `DEBUG_PIN` | 12 | assembly/debug mode when pulled HIGH |
| `BAUD_RATE` | 115200 | host serial |
| `InfoPrint` | 0 | set 1 to echo received commands / parse errors |
| `timeOffset` | 50 ms | mission step interval compensation |

### Gait geometry — `src/BodyCtrl.cpp` (linkage constants + walk defaults)

Linkage lengths (`linkage_s/a/b/c/d/e/f` = 12.2 / 40 / 40 / 39.8153 / 31.775 / 30.8076 / wiggleError) match the physical leg — do not change unless you redesign the mechanism. Walk defaults: `WALK_HEIGHT_MAX/MIN` 110/75, `WALK_HEIGHT` 95, `WALK_LIFT` 9, `WALK_RANGE` 40, `WALK_EXTENDED_X/Z` 16/25, `BALANCE_P` 0.72.

### Runtime tuning (no recompile)

- `{"T":115,"delay":5,"iterate":0.02}` — step timing / smoothness
- `{"T":116,"maxHeight":110,"minHeight":75,"height":95,"lift":9,"range":40,"acc":5,"extendedX":16,"extendedZ":25,"sideMax":30,"massAdjust":21}` — all gait geometry
- `{"T":400,...}` — WiFi AP/STA credentials, persisted to the `boot` mission

## Codemap

```
wavego_pro_platformio/WAVEGO_Pro_PlatformIO/
├── platformio.ini        # esp32dev / espressif32 / arduino, lib_deps
└── src/
    ├── main.cpp          # entry: JSON dispatcher (47 cmds), serialCtrl parser, /js web
    │                     #   endpoint, boot-mission logic, buzzer, steady-mode loop,
    │                     #   T:1001 telemetry, debug-pin detect
    ├── Config.h          # pin map, baud, all CMD_* macro definitions with examples
    ├── BodyCtrl.cpp/.h   # IK (linkage math), gait engine (simple/triangular), demo
    │                     #   actions, balancing, calibration; jointID servo map
    ├── RGBLight.cpp/.h   # 2x WS2812 NeoPixel wrapper
    ├── ScreenCtrl.cpp/.h # SSD1306 128x32, 4-line text buffer
    ├── FilesCtrl.cpp/.h  # LittleFS mission files (.mission), step CRUD, scan/format
    ├── Wireless.cpp/.h   # WiFi AP/STA, ESP-NOW send/recv + JSON callback
    ├── devs.h            # ICM20948 IMU (acc update for balancing) + INA219 battery
    ├── web_page.h        # embedded web UI (HTML/JS): pad, function buttons, JSON console
    └── src.ino           # Arduino IDE compatibility shim
```

Joint map: index 0-11 → servo ID `{53,52,51, 41,42,43, 23,22,21, 31,32,33}` (LF, LH, RF, RH × A/B/H from hip). Legs for `T:113`: 1=front-left, 2=hind-left, 3=front-right, 4=hind-right. Positions 0-1023, mid = 511.

## License

GPL-3.0 — see [root README](../README.md).
