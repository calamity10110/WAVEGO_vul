# WAVEGO Pro — Project Brief (compressed reference)

Condensed from the full READMEs; use this as the fast entry point. Detail lives in
[`../README.md`](../README.md) (33 KB) → [`../ugv_rpi/README.md`](../ugv_rpi/README.md) (25 KB)
→ component READMEs; the Context Mode KB indexes `docs:root-readme` for search.

## What this is

A fork of the Waveshare WAVEGO Pro bionic dog: ESP32 lower-computer firmware, Raspberry Pi
upper-computer app, plus an embodied-agent stack. ~28.5 K lines across 5 components.

| Component | Path | Size | Role |
|---|---|---|---|
| RPi app | `ugv_rpi/` | 15 629 L / 57 files | Flask + SocketIO + OpenCV web UI, sole UART owner, bypass HTTP API |
| ESP32 firmware | `wavego_pro_platformio/` | 4 486 L / 18 files | IK gait engine (T:117), servos, OLED, RGB, missions, ESP-NOW (PlatformIO) |
| Embodied agent | `agent/` | 3 321 L / 28 files | VLM proposes hex states → deterministic validator is the only motion authority |
| Pet app | `pet/` | 3 169 L / 21 files | Qwen3-VL persona app driving the robot via `wavego_agent` |
| Motion supervisor | `wavego_agent/` | 2 109 L / 14 files | schema/translate/supervisor: one UART owner, T:117 emission |

## Command protocol (48 commands, 9 groups)

JSON objects with integer `T`; source of truth `Config.h` + `main.cpp` dispatcher.
Full table: `wavego_pro_instruction_table.json` / `.xlsx`.

| Group | Count | T codes |
|---|---|---|
| body_motion | 19 | 1, 101–117, 133 |
| files_missions | 11 | 300–309, 399 |
| esp_now | 5 | 410–414 |
| screen_oled | 4 | 202–205 |
| wireless_wifi | 4 | 400–403 |
| system | 2 | 600, 601 |
| rgb_light / buzzer / power | 1+1+1 | 201 / 206 / 207 |

Feedback: `1001` periodic telemetry (5 s), `-106` positions, `-207` battery, `105`/`400` echoes,
`-117` gait-engine distance completion. Key motion codes: `111` level-based gait (stop = `FB:0 LR:0`,
the repo-wide single-stop invariant), `117` parametric engine (crawl/walk/run, speed, heading,
height, stride, distance), `112` functions (sit/handshake/jump/steady), `1` differential vector.

## Services & ports

| Service | Start | Port | Surface |
|---|---|---|---|
| Main app | cron `@reboot` | 5000 | tabbed Web UI (Control/Dashboard/Telemetry/Joystick/Gait/Settings/Photos/Videos), `/api/cmd?sync_ms`, `/api/status`, `/api/cv`, `/api/vlm`, `/api/config`, `/api/telemetry`, MJPEG `/video_feed` |
| Voice assistant | `install_voice.sh` → `wavego-voice` | — | Xiaomi MiMo ASR/TTS (`token-plan-sgp.xiaomimimo.com/v1`, key in `/etc/wavego/mimo.env`), Moonshine offline fallback, phrase grammar → bypass API |
| VLM goal control | `install_vlm.sh` → `wavego-vlm` | 501 | Intern-Decision goals, software-verified 9-command whitelist |
| Failsafe watchdog | `install_failsafe.sh` → `wavego-failsafe` | — | post-mortem UART stop if app dies inside the motion grace window |
| Embodied agent | `agent/run.sh` → `wavego-agent` | 8000 | token-auth dashboard, ESTOP-only write; run **instead of** direct-control sessions |
| Jupyter tutorials | — | 8888 | notebooks 30–31 = HTTP API + telemetry walkthroughs |

## Safety model

- Motion is level-based: robot keeps the last gait until a new command; the only stop is
  `T:111 FB:0 LR:0` (all paths — web, voice, agent, failsafe — end in a halt).
- Voice moves auto-stop after `move_timeout_s` (3 s); failsafe daemon watches `T:111`/`T:1`/`T:117`.
- Agent: validator gates (codebook, format, speed, transition), bad-code streak → forced halt,
  sqlite audit trail; unverified codes quarantined in `state_table.yaml`.
- Gait-engine distance targeting is open-loop odometry (±20 % slip); RUN capped at speed 0.8
  until bench verification (standing hardware TODO).

## Dev checks

- `pytest` — 407 tests (agent, pet, ugv_rpi, wavego_agent), scoped by `pyproject.toml`
- `ruff check .` — clean (0 findings; boot-order E402 in `app.py` is a documented per-file ignore)
- Coverage: `pytest --cov=. --cov-context=test` → `coverage lcov -o coverage.lcov` →
  `repowise coverage add coverage.lcov .coverage` (73 % line coverage, test-to-code map ingested)
- Firmware: PlatformIO build + `tests/test_gait_engine.cpp` host g++ step in CI
- Repowise: index synced at HEAD, doc-drift 0; coverage folded into health scores

## Tech debt (top 5)

1. ~~`moveProcess` CCN 40~~ — split into `applyMoveSpeed` + table-driven `moveVector` (16-combo verified)
2. `ugv_rpi/cv_ctrl.py` health 1.0/10 — camera chain + pure helpers extracted (`cv_camera.py`,
   complexity 27→23); remaining detector/OSD methods are hardware-bound (need a bench session)
3. ~~No coverage report~~ — 73 % line coverage + test-to-code map ingested into repowise
4. Hardware bench verification of gait-engine mode tables (RUN stability, distance accuracy)
5. ~~31 pre-existing ruff findings~~ — all fixed; dead-code flags mostly false positives (entry points, pin tables)

## Docs map

| Question | Doc |
|---|---|
| Install, schematic, wiring, custom actions, camera | `README.md` |
| RPi routes, bypass API, settings, codemap, voice | `ugv_rpi/README.md` |
| Firmware build/flash, T-code API, gait geometry | `wavego_pro_platformio/README.md` |
| Agent loop, safety layers, dashboard | `README.md` §Embodied agent, `agent/DATAFLOW.md` |
| Stock-vs-fork comparison | `docs/comparison-report.md` |
| Command table (48 T-codes) | `wavego_pro_instruction_table.json`/`.xlsx` |
| Hex codebook + thresholds | `state_table.yaml` |
