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

## 3. Get this repo onto the Pi

```bash
sudo apt install -y git
git clone <your-fork-url> ~/WAVEGO_Pro
cd ~/WAVEGO_Pro
```

## 4. Install the pet (one command)

```bash
chmod +x pet/install_pet.sh
sudo ./pet/install_pet.sh          # add --take-uart to auto-stop the
                                   # ugv_rpi web app and claim the UART
```

What it does (~15 min on the Pi, mostly the llama.cpp build + 1.8 GB model
download):

| Step | What | Where |
|---|---|---|
| 1 | apt: `python3-picamera2`, `python3-opencv`, build tools | system |
| 2 | builds llama.cpp (CPU, native) | `/opt/llama.cpp` |
| 3 | downloads [Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF): `Q4_K_M` (~1.1 GB) + `mmproj-Q8_0` vision encoder | `/opt/wavego-pet/models` |
| 4 | venv (`--system-site-packages` so apt OpenCV/camera work) | `/opt/wavego-pet/venv` |
| 5 | deploys `pet/` + `wavego_agent/` + config | `/opt/wavego-pet` |
| 6 | two systemd services | `wavego-pet-vlm` (the VLM), `wavego-pet` (the pet) |

**UART ownership (important):** exactly one process may hold the ESP32's
UART. If the ugv_rpi web app (`wavego.service`) or the agent
(`wavego-agent.service`) is installed, stop it, or re-run the installer with
`--take-uart`. The pet's failsafe coverage: the motion supervisor's telemetry
watchdog, bounded command queues, and pet-safe clamps (walks ≤ 2.5 s at
speed ≤ 0.5, turns ≤ 1.5 s, no full-speed running, ever).

## 5. Try it

**Without hardware (any machine, no camera/VLM/UART):**

```bash
python -m wavego_pet.petserve --mock --duration 70
# prints the exact wire transcript the ESP32 would receive
```

**On the Pi — vision smoke test first:**

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

## 6. Everyday commands

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

## 7. What to expect (honest numbers)

| Thing | Value on Pi 5 (8 GB) |
|---|---|
| Model footprint | ~1.8 GB RAM (Q4_K_M + mmproj Q8_0 + context) |
| Text generation | ~4–5 tokens/s |
| One scene decision | ~10–20 s (image encode + ~30-token reply) |
| Pet "reaction time" | seconds, not frames — like a real pet deciding to care |
| Motion loop | independent 30 Hz; VLM slowness never blocks movement |

## 8. Troubleshooting

- **`camera capture failed` in the log** — `vcgencmd get_camera` should say
  `detected=1`; reseat the ribbon (metal contacts facing the board).
- **VLM timeouts** — `systemctl status wavego-pet-vlm`; first model load
  takes ~30–60 s after boot before the pet's first scene succeeds.
- **`Serial ... PermissionError` / no motion** — something else owns the
  UART: `sudo systemctl stop wavego.service wavego-agent.service`, or
  re-run the installer with `--take-uart`.
- **OOM / killing other services** — the pet needs ~2.5 GB total; don't run
  it alongside the web UI + agent stack on the same boot.
- **Pet does nothing** — `--log-level DEBUG` in the service file; check the
  VLM replies are valid JSON (`journalctl -u wavego-pet | grep scene`).

## 9. Layout

```
pet/
  install_pet.sh          fresh-OS installer (this guide, automated)
  pet_config.yaml         personality + hardware config
  wavego-pet.service      pet service (systemd template)
  wavego-pet-vlm.service  llama-server service (systemd template)
  wavego_pet/             persona, vlm_client, vision, brain, petserve
  tests/                  37 tests, all mocked, run on any machine
wavego_agent/             the motion foundation it stands on (see its README)
```
