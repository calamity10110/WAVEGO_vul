# WAVEGO Pro — Stock vs. This Fork vs. The Field

October 2026 · fork `calamity10110/WAVEGO_vul` @ `9bc5943` · stock baseline
`waveshareteam/WAVEGO_Pro` @ `3d74e86` ("ap update", Jul 2025)

All repo numbers below come from `git diff origin/main..main` and the test
runners on this exact commit; competitor data from vendor pages and reviews
(linked at the end).

---

## 1. Stock vs. this fork

### 1.1 Scale of the change

| Metric | Stock | This fork |
|---|---|---|
| Files tracked | 188 | 304 |
| Python/JS source files | 16 | 95 |
| Lines changed vs stock | — | **+18,002 / −2,767 across 154 files** |
| Automated tests | **0** | 259 pytest (86 ugv_rpi · 90 wavego_agent · 83 pet) + ~55 standalone agent checks incl. a 23-attack red-team suite |
| CI | none | GitHub Actions release pipeline: full test matrix, ESP32 PlatformIO firmware build, tar+sha256 RPi bundle, flash manifest, tag releases |
| Static analysis | none | Repowise index: 0 dead symbols, 0 security findings (full 104-commit history scan), avg code health 8.13/10 |

### 1.2 What the fork adds, layer by layer

**Safety (the reason the fork exists).** Stock ships a level-based gait
protocol: if the controlling process dies mid-walk, the ESP32 keeps the last
gait forever — the only stop is the power switch. The fork adds a failsafe
daemon, an E-stop that latches and drops the queue, a telemetry watchdog
safety stop, bounded command queues, and pet-safe clamps on every duration
and speed no matter what config or LLM asks for.

**`wavego_agent/` — semantic motion layer.** Natural-language-ish commands
("walk forward 2 s, turn left, lean toward me") parsed into a typed schema,
translated to firmware commands with cadence management (speed maps to gait
cadence, not magnitude — the hardware has no magnitude channel) and the
T:133 body-lean action (this chassis has no gimbal; "look" is a whole-body
lean). Closed-loop turn/approach behaviors with bearing and yaw-wraparound
handling. 90 tests, injected clock, no hardware needed.

**`pet/` — an actual pet.** Local Qwen3-VL-2B (llama.cpp, not Ollama — its
mmproj path is broken for Qwen-VL) turns the camera into scenes
(person/gesture/position), a persona engine decides pet behavior, and a
validator model gives a second opinion before any motion — with a hard rule
that validator and proposer can never be the same model. Inference cost is
engineered down three ways: byte-stable prompt prefix for llama-server's
KV cache (`--cache-reuse 256`), boot warmup (with the race where the camera
hadn't delivered its first frame yet found and fixed in review), and
change-gated inference (32×32 grayscale mean-diff skips the VLM entirely on
a static scene). Optional DSI LCD status card, system stats, USB-or-UART
ESP32 auto-detection, and an installer with interactive setup choices.

**`agent/` — autonomous LLM agent.** Mission-driven loop (perceive →
deliberate → execute) with an audit log, a learner, a dashboard on :8000,
and its own fuzz + red-team verdict suites. This is the experimental layer;
`wavego_agent` is the stable foundation under it.

**`ugv_rpi/` hardening.** The stock Flask app keeps its role (control pad,
dashboard, telemetry, joystick, gait tuning, camera CV modes) but gained 86
tests, a settings API, voice control, Jupyter notebooks for the HTTP bypass
API, and the failsafe installer.

**Docs & reproducibility.** Reverse-engineered instruction tables
(`wavego_pro_instruction_table.json` / `.xlsx`, `state_table.yaml`) covering
every ESP32 command, per-package READMEs, and a CI that produces a
flash-ready bundle with checksums on every push.

### 1.3 Honest accounting — what the fork does *not* change

- **Gaits are still the firmware's scripted IK tables.** No learned/RL
  locomotion; the fork's motion work is orchestration, cadence, and safety
  around the vendor's gait engine.
- **`cv_ctrl.py` remains the worst-health file (1.9/10)** — inherited stock
  code; the fork tests around it rather than rewriting it.
- **Hardware-verified items still open** (flagged in code, one session on
  the robot each): lean sign constants, STEP_DELAY cadence bounds, and the
  approach-turn direction on the physical chassis.
- **VLM throughput is physics**: ~4–5 tok/s on a Pi 5 CPU means one scene
  decision costs 10–20 s. The change-gate and prompt cache make that
  livable; they don't remove it.

---

## 2. This fork vs. the field

The realistic competitive set for a 12-DOF, Raspberry-Pi-class desktop
quadruped: Petoi Bittle 2/X, Luwu XGO2 (XGO-Mini2), and — one tier up in
price and capability — Unitree Go2.

| | **WAVEGO Pro (this fork)** | WAVEGO Pro (stock) | Petoi Bittle 2 / X | Luwu XGO2 | Unitree Go2 (Pro/EDU) |
|---|---|---|---|---|---|
| Street price (approx.) | $243–447 hardware, software free | same | ~$236–300 | $675–849 (radar variant $1,599) | $2,800 consumer · $11k+ EDU |
| DOF / drive | 12 × 2.3 kg·cm serial-bus servos w/ feedback | same | 8–9 DOF, 290 g class | 12 DOF + optional arm/gripper | 8–12 joint motors, RL gaits, ~15 kg |
| Compute | ESP32 + Raspberry Pi 5 | ESP32 + Pi 4/5 | MCU-first, Pi optional saddle | Pi CM4 onboard | Jetson-class (EDU), proprietary mainboard |
| On-device AI | **Qwen3-VL-2B scene understanding, dual-model validation, change-gated inference** | OpenCV modes (line/face follow) | offline voice commands | face/gesture demos, Blockly | GPT-style integration, LiDAR SLAM |
| Autonomous stack | semantic command layer + persona pet + LLM mission agent | remote-control web app | skill/instinct primitives, strong OSS community | Python/ROS/Blockly SDKs | full SDK, EDU unlock for research |
| Safety engineering | failsafe daemon, estop latch, watchdog, clamps, bounded queues | **none — gait outlives controller** | basic | basic | extensive (hardware-class) |
| Tests / CI | 259+ tests, firmware+bundle CI, red-team suite | none | community firmware tests | limited | n/a (closed) |
| Open source depth | full app+agent+test stack, instruction tables, PlatformIO build | app source only | very open (OpenCat) | SDK open, firmware closed | closed |
| ROS | no | no | partial ports | yes | yes (EDU) |

### Where the fork wins

**Price-per-capability at the "AI pet" game.** The only sub-$500 robot in
this set that does on-device *scene understanding with a validated
VLM→behavior pipeline*. Bittle has no vision-language model at any price;
XGO2 gets gesture demos at ~2–3× the cost; Go2's big-model features start
at $2,800 and its real developer tier is $11k+.

**Engineering integrity.** No competitor at this price publishes a test
suite, a red-team suite against its own autonomy, a failsafe for its own
documented failure mode, or reverse-engineered protocol tables. That's
normally EDU/research-tier practice.

**A real local-LLM stack.** Qwen3-VL running on-device with a
proposer/validator split — cheap robots ship OpenCV; expensive robots ship
cloud. Doing local VLM with second-opinion validation at Bittle money is
the fork's genuine differentiator.

### Where competitors win

- **Bittle 2/X**: far larger community (OpenCat lineage), polished tricks
  (backflips), offline voice that works out of the box, lighter (290 g).
  If you want a nimble desk dog and don't need vision reasoning, it's
  cheaper and more proven.
- **XGO2**: aluminum chassis, 6D posture control, optional arm+gripper,
  LiDAR variant, official ROS support — a better *research/education
  platform* out of the box. This fork has no ROS story yet.
- **Unitree Go2**: different league — RL locomotion, real SLAM, 3 m/s,
  payload. The fork's VLM pet is a personality layer on scripted gaits;
  Go2 is an actual dynamic robot. Comparing them is category error, listed
  only for budget context.

### Bottom line

Stock WAVEGO Pro is a capable remote-control dog with an unsafe failure
mode and zero test surface. This fork turns the same $300-ish hardware into
the cheapest available platform for local-VLM embodied agents, at the cost
of ~18k lines you now maintain. Against the field it is the value pick for
"on-device AI pet / agent research"; XGO2 remains the pick if you need ROS
and an arm today, Go2 if you have $3k–15k and need dynamic locomotion.

---

## Sources

- [Waveshare WAVEGO Pro product page](https://www.waveshare.com/wavego-pro.htm) · [Waveshare wiki](https://www.waveshare.com/wiki/WAVEGO_Pro) · [stock GitHub](https://github.com/waveshareteam/WAVEGO_Pro)
- [CNX-Software WAVEGO Pro review (Aug 2025)](https://www.cnx-software.com/2025/08/13/wavego-pro-12-dof-bionic-robot-dog-supports-esp-now-and-ai-vision-through-raspberry-pi-4-5-sbc)
- [Petoi Bittle](https://www.petoi.com/products/petoi-bittle-robot-dog) · [RobotShop Bittle vs XGO comparison](https://community.robotshop.com/blog/show/robo-pup-showdown-petoi-bittle-vs-xgo-mini)
- [XGO Mini2s (RobotShop)](https://www.robotshop.com/products/xgo-mini2-desktop-ai-robot-dog-us)
- [Unitree Go2 shop](https://shop.unitree.com/products/unitree-go2) · [Unitree developer docs](https://support.unitree.com/home/en/developer) · [Go2 series guide](https://globaldronehq.com/blogs/guides/unitree-go2-series-guide)

*Generated 2026-10-24 from the state of `main` at `9bc5943`; per-suite test
counts re-verified against the runners the same day.*
