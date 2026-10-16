# WAVEGO Pet — a Qwen3-VL robot pet for the Raspberry Pi 5

Your WAVEGO Pro becomes a pet: it sees through its camera (Qwen3-VL-2B
running locally on the Pi 5), has a personality (moods, greetings, tricks,
following, naps), and moves through the `wavego_agent` motion supervisor —
so every step, jump and glance is validated, rate-limited and safety-clamped
before it reaches the ESP32.

```
camera (448px JPEG) ──▶ Qwen3-VL-2B (llama-server, local)
                            │  "person visible, standing left, waving"
                            ▼
                       PetBrain + persona ── semantic commands
                            │   {"action":"walk","direction":"forward",...}
                            ▼
                  wavego_agent MotionSupervisor (validation, cooldowns,
                            │    emergency stop, single UART owner)
                            ▼
                     ESP32 (native WAVEGO JSON over UART)
```

No cloud, no API keys: the VLM runs on-device (Apache-2.0 model GGUFs).

---

## 1. Flash a fresh Raspberry Pi 5 OS

1. **Raspberry Pi Imager** → *Raspberry Pi OS (64-bit)* — Bookworm or later.
   (Lite is fine; desktop not required.)
2. In Imager's ⚙ settings: enable SSH, set hostname `wavego-pet`, configure
   Wi-Fi (if not using ethernet).
3. Flash, insert SD, boot, SSH in: `ssh pi@wavego-pet`.

## 2. Prepare the UART + camera (once)

```bash
sudo raspi-config nonint do_serial_hw 0     # enable serial hardware
sudo raspi-config nonint do_serial_cons 1   # keep login shell OFF the UART
sudo reboot
```

The camera needs nothing extra on Bookworm — `picamera2` ships with the OS.

If you previously ran `ugv_rpi/setup.sh`, the UART is already configured.

## 3. Hardware hookup

**Camera — CAM0 or CAM1, both work.** The Pi 5 has two MIPI connectors
(silk-screened CAM/DISP 0 and 1); the camera runs on either. Seat the
ribbon with the metal contacts facing the board and the blue stiffener
facing the latch's pull-tab. The installer verifies detection and prints
which port it found (`rpicam-hello --list-cameras`).

**ESP32 control board — USB or UART, your choice.**

- *USB (recommended for bring-up)*: a cable from the board's USB port to a
  Pi USB port. It appears as `/dev/ttyACM*` or `/dev/ttyUSB*` — no pins, no
  raspi-config, and the same JSON protocol.
- *UART*: the classic 3-wire hookup to the GPIO header — ESP32 TX → Pi RX,
  ESP32 RX → Pi TX (crossed!), GND → GND.

The pet's config ships with `uart.device: auto`, which prefers the USB
device when one is present and falls back to the GPIO UART
(`/dev/ttyAMA0` on Pi 5). Force one explicitly in `pet_config.yaml` if you
have both connected. The installer adds your user to the `dialout` group
(needed for USB serial) — log out and back in after install.

**Optional DSI LCD — DISP0 or DISP1, either works.** If a DSI display is
attached when the installer runs, it installs the status UI automatically
(You'll see the pet's state, what it last saw, and battery voltage on the
screen). Not attached? Add it anytime and re-run the installer. Disable it
without unplugging via `display.enabled: false` in `pet_config.yaml`.

## 4. Get this repo onto the Pi

```bash
sudo apt install -y git
git clone <your-fork-url> ~/WAVEGO_Pro
cd ~/WAVEGO_Pro
```

## 5. Install the pet (one command)

```bash
chmod +x pet/install_pet.sh
sudo ./pet/install_pet.sh          # interactive setup; --yes accepts defaults
```

**Setup choices** (asked interactively, or passed as flags — `--yes`
accepts all defaults):

| Choice | Flag | Options (default) |
|---|---|---|
| Camera MIPI port | `--camera` | `dsi1` (default) or `dsi0` |
| Status LCD | `--display` | `none` (default) or the **other** connector — the camera takes one port, the display must take the other |
| Display size | `--display-size` | `waveshare-2` 480x480 (default), `waveshare-2.8` 480x640, `waveshare-4` 720x720, `waveshare-7` 1024x600, or custom `WxH` |
| Scene VLM (proposer) | `--vlm` | `host` (default — local llama-server) or `cloud` (needs `--vlm-url`, `--vlm-model`, `--vlm-api-key-env`) |
| Validator | `--validator` | `software` (default — no second model) or `host`/`cloud` |

Example — cloud VLM with a local validator model:

```bash
sudo ./pet/install_pet.sh --take-uart   --camera dsi1 --display dsi0 --display-size waveshare-4   --vlm cloud --vlm-url https://api.openai.com/v1   --vlm-model gpt-4o-mini --vlm-api-key-env OPENAI_API_KEY   --validator host --validator-url http://127.0.0.1:8081   --validator-model smolvlm2
```

What it does (~15 min on the Pi, mostly the llama.cpp build + 1.8 GB model
download):

| Step | What | Where |
|---|---|---|
| 1 | apt: `python3-picamera2`, `python3-opencv`, build tools | system |
| 2 | hardware checks: camera port, ESP32 link (USB/UART), optional DSI LCD | printed to console |
| 2 | builds llama.cpp (CPU, native) | `/opt/llama.cpp` |
| 3 | downloads [Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF): `Q4_K_M` (~1.1 GB) + `mmproj-Q8_0` vision encoder | `/opt/wavego-pet/models` |
| 4 | venv (`--system-site-packages` so apt OpenCV/camera work) | `/opt/wavego-pet/venv` |
| 5 | deploys `pet/` + `wavego_agent/` + config | `/opt/wavego-pet` |
| 6 | deploys pet + wavego_agent + config | `/opt/wavego-pet` |
| 7 | two systemd services | `wavego-pet-vlm` (the VLM), `wavego-pet` (the pet) |

**UART ownership (important):** exactly one process may hold the ESP32's
UART. If the ugv_rpi web app (`wavego.service`) or the agent
(`wavego-agent.service`) is installed, stop it, or re-run the installer with
`--take-uart`. The pet's failsafe coverage: the motion supervisor's telemetry
watchdog, bounded command queues, and pet-safe clamps (walks ≤ 2.5 s at
speed ≤ 0.5, turns ≤ 1.5 s, no full-speed running, ever).

### The validator rule (read this once)

The VLM proposes what it sees; the validator double-checks the claim
before the pet acts. **The same model may never serve as both** — one
model grading its own homework is how hallucinated greetings happen.
`petserve` refuses to start with such a config, and the installer errors
out early. Your options:

- `software` (default) — no second model: a claim must repeat on 2
  consecutive frames before the pet acts (temporal consistency).
- `host` — a second LOCAL model (any port/model, e.g. a small SmolVLM on
  `:8081` while Qwen3-VL serves `:8080`).
- `cloud` — a hosted OpenAI-compatible endpoint (API key read from the
  env var you name).

A validator that fails to answer downgrades the scene to "nothing seen":
the pet stays calm.

## 6. Try it

**Without hardware (any machine, no camera/VLM/UART):**

```bash
python -m wavego_pet.petserve --mock --duration 70
# prints the exact wire transcript the ESP32 would receive
```

**On the Pi — full hardware doctor first:**

```bash
/opt/wavego-pet/venv/bin/python -m wavego_pet.petserve --doctor
```

Checks the camera (and prints which MIPI port it's on), the ESP32
connection (USB or GPIO UART), the VLM endpoint, and the DSI LCD — PASS,
WARN or SKIP per line, with fix hints on WARN.

**Vision smoke test:**

```bash
sudo /opt/llama.cpp/build/bin/llama-mtmd-cli \
  -m /opt/wavego-pet/models/Qwen3VL-2B-Instruct-Q4_K_M.gguf \
  --mmproj /opt/wavego-pet/models/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf \
  --image some_photo.jpg -p "Describe this image in one sentence."
```

Expect the answer in ~10–30 s (Pi 5 CPU: ~4–5 tok/s text + image encode).
If that works, start the pet:

```bash
sudo systemctl start wavego-pet
journalctl -u wavego-pet -f        # watch it think
journalctl -u wavego-pet-vlm -f    # watch the VLM
```

Hold something up to the camera and wave. The pet greets new people (walk
+ leans up toward you + jump), does a spin trick when you wave, shuffles
toward/turns
after you, glances around when bored, and sits down to sleep after 5 quiet
minutes. Walk away — it dozes. Walk back — it wakes.

## 7. Everyday commands

```bash
sudo systemctl stop wavego-pet        # park the pet (sends a final sit + stop)
sudo systemctl start wavego-pet       # wake it up again
sudo systemctl disable wavego-pet     # no pet on boot
journalctl -u wavego-pet -f           # live behavior log
tail -f /opt/wavego-pet/session.jsonl # JSONL command/telemetry record (if enabled)
```

Change its personality: edit `/opt/wavego-pet/pet_config.yaml`
(cooldowns, walk speed/duration, fidget/sleep timers), then
`sudo systemctl restart wavego-pet`. All values are clamped to safe bounds
in code — no config can make the pet sprint or jump-spam.

## 8. What to expect (honest numbers)

| Thing | Value on Pi 5 (8 GB) |
|---|---|
| Model footprint | ~1.8 GB RAM (Q4_K_M + mmproj Q8_0 + context) |
| Text generation | ~4–5 tokens/s |
| One scene decision | ~10–20 s (image encode + ~30-token reply) |
| Pet "reaction time" | seconds, not frames — like a real pet deciding to care |
| Motion loop | independent 30 Hz; VLM slowness never blocks movement |

## 9. Troubleshooting

- **`camera capture failed` / doctor says no camera** — run
  `rpicam-hello --list-cameras`; if empty, reseat the ribbon (metal
  contacts facing the board, blue stiffener facing the latch) and try the
  other MIPI connector — CAM0/DSI0 and CAM1/DSI1 are equivalent on the Pi 5.
- **VLM timeouts** — `systemctl status wavego-pet-vlm`; first model load
  takes ~30–60 s after boot before the pet's first scene succeeds.
- **`Serial ... PermissionError` / no motion** — run the doctor. If USB:
  make sure you're in the `dialout` group (installer adds it; re-login).
  If the ugv_rpi web app or agent owns the link:
  `sudo systemctl stop wavego.service wavego-agent.service`, or re-run the
  installer with `--take-uart`.
- **Status LCD blank** — doctor prints the DRM connector when a DSI display
  is detected; if it is but the screen stays dark, check `display.enabled`
  in `pet_config.yaml` is not `false`, and that `python3-pygame` is
  installed (the installer adds it only when a display is present).
- **OOM / killing other services** — the pet needs ~2.5 GB total; don't run
  it alongside the web UI + agent stack on the same boot.
- **Pet does nothing** — `--log-level DEBUG` in the service file; check the
  VLM replies are valid JSON (`journalctl -u wavego-pet | grep scene`).

## 10. Research notes — TinyVLM adoption

Reviewed: [anananan116/TinyVLM](https://github.com/anananan116/TinyVLM)
(MIT) — a 1.5B VLM (Llama-3.2-1B + frozen CLIP ViT-L/14 + MLP adapter,
images pooled to 64 tokens) trained on 2x RTX 3090, scoring 1370 on MME.
Its evals confirm what our design already bets on, and three of its
techniques are adopted:

- **Adopted — coarse spatial queries only.** TinyVLM is strong on
  existence/color/scene, weak on OCR/count/position. Our scene prompt asks
  exactly that: person yes/no, coarse position (left/center/right),
  gesture — never counts or precise coordinates.
- **Adopted — fixed small image budget + center-crop preprocessing.**
  TinyVLM center-crops or pads to a fixed 448x448; our camera frames are
  captured at 448x448 and `vision.fit_square()` squares any non-square
  source the same way, keeping the vision-token bill (and Pi 5 encode
  time) flat.
- **Adopted — proposer/verifier split.** Small-VLM single-frame claims
  are noisy; the validator layer (software consistency / second model)
  is the mitigation, matching TinyVLM's low position reliability.
- **Planned — fine-tune a pet-scene VLM from our own logs.** The JSONL
  session logs are (frame, scene, action) triples: TinyVLM's two-stage
  recipe (freeze vision encoder, train adapter, then SFT) is a viable path
  to a smaller, faster, domain-tuned pet VLM later. Not built yet.

## 11. Layout

```
pet/
  install_pet.sh          fresh-OS installer (this guide, automated; also
                          verifies camera, ESP32 link, and DSI LCD)
  pet_config.yaml         personality + hardware config (uart auto, display auto)
  wavego-pet.service      pet service (systemd template)
  wavego-pet-vlm.service  llama-server service (systemd template)
  wavego_pet/             persona, vlm_client, validator, vision, display,
                          systemstats, brain, petserve
  tests/                  74 tests, all mocked, run on any machine
wavego_agent/             the motion foundation it stands on (see its README)
```
