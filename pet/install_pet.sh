#!/bin/bash
# install_pet.sh — fresh Raspberry Pi 5 OS (Bookworm 64-bit) -> robot pet.
#
# What it does:
#   1. apt packages (picamera2 + OpenCV come from apt, never pip on Pi OS)
#   2. hardware checks: camera (CAM0/CAM1), ESP32 board (USB or GPIO UART),
#      optional DSI LCD (DSI0/DSI1)
#   3. builds llama.cpp (CPU, native) into /opt/llama.cpp
#   4. downloads Qwen3-VL-2B-Instruct GGUF (~1.1 GB) + vision encoder
#   5. venv at /opt/wavego-pet/venv (--system-site-packages: apt OpenCV/camera)
#   6. installs this repo's pet/ + wavego_agent/ to /opt/wavego-pet
#   7. systemd services: wavego-pet-vlm (llama-server) + wavego-pet (petserve)
#
# Usage:  sudo ./install_pet.sh [--skip-build] [--take-uart]
#   --skip-build  reuse an existing /opt/llama.cpp build (re-runs are fast)
#   --take-uart   disable the ugv_rpi web app services so the pet owns the link
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PET_DIR="/opt/wavego-pet"
LLAMA_DIR="/opt/llama.cpp"
MODEL_DIR="$PET_DIR/models"
HF_BASE="https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main"
MODEL_GGUF="Qwen3VL-2B-Instruct-Q4_K_M.gguf"
MMPROJ_GGUF="mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"
PET_USER="${SUDO_USER:-pi}"

SKIP_BUILD=0
TAKE_UART=0
CAMERA_PORT="dsi1"          # setup choice 1: camera MIPI port (default dsi1)
DISPLAY_PORT="none"         # setup choice 2: display on the OTHER port (default none)
DISPLAY_SIZE="480x480"      # setup choice 3: status card size/resolution
VLM_PROVIDER="host"         # setup choice 4: scene proposer (host|cloud)
VLM_URL="" VLM_MODEL="" VLM_KEY_ENV=""
VALIDATOR_PROVIDER="software"  # setup choice 5: second opinion (software|host|cloud)
VALIDATOR_URL="" VALIDATOR_MODEL="" VALIDATOR_KEY_ENV=""
ASSUME_YES=0

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --take-uart)  TAKE_UART=1 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    --camera)     CAMERA_PORT="$2"; shift ;;
    --display)    DISPLAY_PORT="$2"; shift ;;
    --display-size) DISPLAY_SIZE="$2"; shift ;;
    --vlm)        VLM_PROVIDER="$2"; shift ;;
    --vlm-url)    VLM_URL="$2"; shift ;;
    --vlm-model)  VLM_MODEL="$2"; shift ;;
    --vlm-api-key-env) VLM_KEY_ENV="$2"; shift ;;
    --validator)  VALIDATOR_PROVIDER="$2"; shift ;;
    --validator-url) VALIDATOR_URL="$2"; shift ;;
    --validator-model) VALIDATOR_MODEL="$2"; shift ;;
    --validator-api-key-env) VALIDATOR_KEY_ENV="$2"; shift ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

# --- resolve + validate setup choices (prompt interactively for anything
# --- not passed as a flag, when a terminal is attached)
ask() { # var prompt default
  local __var="$1" __prompt="$2" __default="$3" __ans=""
  if [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
    eval "$__var=\"\$__default\""
  else
    read -r -p "$__prompt [$__default]: " __ans || __ans=""
    eval "$__var=\"\${__ans:-\$__default}\""
  fi
}

OTHER_PORT="dsi0"; [ "$CAMERA_PORT" = "dsi0" ] && OTHER_PORT="dsi1"
case "$CAMERA_PORT" in dsi0|dsi1) ;; *) echo "bad --camera $CAMERA_PORT (dsi0|dsi1)"; exit 2 ;; esac
if [ "$DISPLAY_PORT" = "none" ] || [ -z "$DISPLAY_PORT" ]; then
  if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; then
    ask DISPLAY_PORT "Optional status LCD on the other MIPI port ($OTHER_PORT)? [$OTHER_PORT|none]" "none"
  fi
fi
case "$DISPLAY_PORT" in none|"$OTHER_PORT") ;; *) echo "bad --display $DISPLAY_PORT (must be $OTHER_PORT or none — camera uses $CAMERA_PORT)"; exit 2 ;; esac

DISPLAY_W=480; DISPLAY_H=480
if [ "$DISPLAY_PORT" != "none" ]; then
  case "$DISPLAY_SIZE" in
    waveshare-2)   DISPLAY_W=480;  DISPLAY_H=480 ;;
    waveshare-2.8) DISPLAY_W=480;  DISPLAY_H=640 ;;
    waveshare-4)   DISPLAY_W=720;  DISPLAY_H=720 ;;
    waveshare-7)   DISPLAY_W=1024; DISPLAY_H=600 ;;
    *x*)
      DISPLAY_W="${DISPLAY_SIZE%x*}"; DISPLAY_H="${DISPLAY_SIZE#*x}"
      [ "$DISPLAY_W" -ge 100 ] 2>/dev/null && [ "$DISPLAY_H" -ge 100 ] 2>/dev/null \
        || { echo "bad --display-size $DISPLAY_SIZE (preset or WxH >= 100x100)"; exit 2; } ;;
    *) echo "bad --display-size $DISPLAY_SIZE"; exit 2 ;;
  esac
fi

case "$VLM_PROVIDER" in host|cloud) ;; *) echo "bad --vlm $VLM_PROVIDER (host|cloud)"; exit 2 ;; esac
if [ "$VLM_PROVIDER" = "cloud" ] && { [ -z "$VLM_URL" ] || [ -z "$VLM_MODEL" ]; }; then
  if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; then
    [ -z "$VLM_URL" ]   && ask VLM_URL   "Cloud VLM base URL" "https://api.openai.com/v1"
    [ -z "$VLM_MODEL" ] && ask VLM_MODEL "Cloud VLM model"    "gpt-4o-mini"
    [ -z "$VLM_KEY_ENV" ] && ask VLM_KEY_ENV "Env var holding the API key" "OPENAI_API_KEY"
  else
    echo "--vlm cloud needs --vlm-url and --vlm-model"; exit 2
  fi
fi

case "$VALIDATOR_PROVIDER" in software|host|cloud) ;; *) echo "bad --validator $VALIDATOR_PROVIDER"; exit 2 ;; esac
if [ "$VALIDATOR_PROVIDER" != "software" ]; then
  if [ "$VALIDATOR_PROVIDER" = "$VLM_PROVIDER" ] \
      && [ "$VALIDATOR_URL" = "$VLM_URL" ] && [ -n "$VALIDATOR_URL" ] \
      && [ "$VALIDATOR_MODEL" = "$VLM_MODEL" ] && [ -n "$VALIDATOR_MODEL" ]; then
    echo "ERROR: the validator may not use the same model as the VLM"; exit 2
  fi
  if { [ -z "$VALIDATOR_URL" ] || [ -z "$VALIDATOR_MODEL" ]; } \
      && { [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; }; then
    ask VALIDATOR_URL     "Validator base URL"  "$VLM_URL"
    ask VALIDATOR_MODEL   "Validator model (must differ from $VLM_MODEL)" ""
    while [ "$VALIDATOR_MODEL" = "$VLM_MODEL" ] && [ -n "$VALIDATOR_MODEL" ]; do
      ask VALIDATOR_MODEL "Same model refused — pick another" ""
    done
    [ -z "$VALIDATOR_KEY_ENV" ] && ask VALIDATOR_KEY_ENV "Validator API key env (blank if none)" ""
  elif { [ -z "$VALIDATOR_URL" ] || [ -z "$VALIDATOR_MODEL" ]; }; then
    echo "--validator $VALIDATOR_PROVIDER needs --validator-url and --validator-model"; exit 2
  fi
fi

SETUP_NOTE="vlm=$VLM_PROVIDER validator=$VALIDATOR_PROVIDER"
if [ "$DISPLAY_PORT" != "none" ]; then
  SETUP_NOTE="display=$DISPLAY_PORT (${DISPLAY_W}x${DISPLAY_H}) $SETUP_NOTE"
fi
echo "Setup choices: camera=$CAMERA_PORT $SETUP_NOTE"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

echo "==> [1/7] apt packages"
apt-get update -qq
apt-get install -y -qq \
  python3-venv python3-pip python3-picamera2 python3-opencv \
  git cmake build-essential curl
usermod -aG dialout,video "$PET_USER" || true

echo "==> [2/7] hardware checks"

echo "    [camera] configured for $CAMERA_PORT (the other MIPI connector,"
echo "             $OTHER_PORT, stays free for the optional display)"
CAM_OUT=""
if command -v rpicam-hello >/dev/null 2>&1; then
  CAM_OUT="$(rpicam-hello --list-cameras 2>&1 || true)"
elif command -v rpicam-still >/dev/null 2>&1; then
  CAM_OUT="$(rpicam-still --list-cameras 2>&1 || true)"
fi
if [ -n "$CAM_OUT" ] && ! echo "$CAM_OUT" | grep -qi "no cameras"; then
  echo "    camera OK:"
  echo "$CAM_OUT" | sed 's/^/      /'
else
  echo "    WARNING: no camera detected."
  echo "      - plug the camera into $CAMERA_PORT (or either connector,"
  echo "        and re-run with the matching --camera)"
  echo "      - re-seat the ribbon: metal contacts facing the board, blue"
  echo "        stiffener facing the latch; then re-run this installer"
fi

echo "    [ESP32 control board] (USB or GPIO UART — both supported)"
SERIAL_FOUND=0
for d in /dev/ttyACM* /dev/ttyUSB* /dev/ttyAMA0 /dev/serial0; do
  if [ -e "$d" ]; then echo "      found: $d"; SERIAL_FOUND=1; fi
done
if [ "$SERIAL_FOUND" -eq 0 ]; then
  echo "      WARNING: no serial device found. Connect the ESP32 board via"
  echo "      its USB port (recommended) or wire TX/RX/GND to the GPIO UART."
fi
echo "      pet config uart.device=auto prefers USB, then the GPIO UART."

echo "    [optional DSI LCD] ($DISPLAY_PORT)"
DSI_DEV="$(ls /sys/class/drm 2>/dev/null | grep -m1 DSI || true)"
if [ "$DISPLAY_PORT" = "none" ]; then
  echo "      disabled by setup choice — add one later:"
  echo "      sudo ./install_pet.sh --display $OTHER_PORT --yes"
elif [ -n "$DSI_DEV" ]; then
  echo "      DSI display detected: $DSI_DEV ($DISPLAY_W x $DISPLAY_H) —"
  echo "      installing status UI"
  apt-get install -y -qq python3-pygame
else
  echo "      WARNING: status card configured for $DISPLAY_PORT but no DSI"
  echo "      display detected — installing the UI anyway; the pet skips it"
  echo "      until the panel is attached"
  apt-get install -y -qq python3-pygame
fi

echo "==> [3/7] llama.cpp (CPU build, native flags)"
if [ "$SKIP_BUILD" -eq 1 ] && [ -x "$LLAMA_DIR/build/bin/llama-server" ]; then
  echo "    reusing existing build"
else
  if [ -d "$LLAMA_DIR/.git" ]; then
    git -C "$LLAMA_DIR" pull --ff-only
  else
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
  fi
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
    -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF
  cmake --build "$LLAMA_DIR/build" --target llama-server llama-mtmd-cli -j4
fi

echo "==> [4/7] model download ($MODEL_GGUF + $MMPROJ_GGUF)"
mkdir -p "$MODEL_DIR"
for f in "$MODEL_GGUF" "$MMPROJ_GGUF"; do
  if [ ! -s "$MODEL_DIR/$f" ]; then
    curl -fL --retry 3 -o "$MODEL_DIR/$f" "$HF_BASE/$f"
  else
    echo "    $f already present"
  fi
done

echo "==> [5/7] python venv (--system-site-packages: apt picamera2/cv2)"
mkdir -p "$PET_DIR"
python3 -m venv --system-site-packages "$PET_DIR/venv"
"$PET_DIR/venv/bin/pip" install --upgrade pip --quiet
"$PET_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/pet/requirements.txt"

echo "==> [6/7] deploy pet + wavego_agent"
mkdir -p "$PET_DIR"
cp -r "$REPO_DIR/pet/wavego_pet"      "$PET_DIR/"
cp -r "$REPO_DIR/wavego_agent"        "$PET_DIR/"
cp    "$REPO_DIR/pet/pet_config.yaml" "$PET_DIR/"

# write the setup choices into the deployed config
PET_USER="$PET_USER" PET_CFG="$PET_DIR/pet_config.yaml" \
CAMERA_PORT="$CAMERA_PORT" DISPLAY_PORT="$DISPLAY_PORT" \
DISPLAY_W="$DISPLAY_W" DISPLAY_H="$DISPLAY_H" \
VLM_PROVIDER="$VLM_PROVIDER" VLM_URL="$VLM_URL" VLM_MODEL="$VLM_MODEL" \
VLM_KEY_ENV="$VLM_KEY_ENV" \
VALIDATOR_PROVIDER="$VALIDATOR_PROVIDER" VALIDATOR_URL="$VALIDATOR_URL" \
VALIDATOR_MODEL="$VALIDATOR_MODEL" VALIDATOR_KEY_ENV="$VALIDATOR_KEY_ENV" \
python3 - <<'PYEOF'
import os

import yaml

cfg_path = os.environ["PET_CFG"]
with open(cfg_path, "r", encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}

cfg.setdefault("camera", {})["port"] = os.environ["CAMERA_PORT"]

disp = cfg.setdefault("display", {})
if os.environ["DISPLAY_PORT"] == "none":
    disp["enabled"] = False
    disp["port"] = "dsi0" if os.environ["CAMERA_PORT"] == "dsi1" else "dsi1"
else:
    disp["enabled"] = "auto"
    disp["port"] = os.environ["DISPLAY_PORT"]
    disp["width"] = int(os.environ["DISPLAY_W"])
    disp["height"] = int(os.environ["DISPLAY_H"])

vlm = cfg.setdefault("vlm", {})
vlm["provider"] = os.environ["VLM_PROVIDER"]
if os.environ["VLM_URL"]:
    vlm["url"] = os.environ["VLM_URL"]
if os.environ["VLM_MODEL"]:
    vlm["model"] = os.environ["VLM_MODEL"]
vlm["api_key_env"] = os.environ["VLM_KEY_ENV"] or None

val = cfg.setdefault("validator", {})
val["provider"] = os.environ["VALIDATOR_PROVIDER"]
val["url"] = os.environ["VALIDATOR_URL"] or None
val["model"] = os.environ["VALIDATOR_MODEL"] or None
val["api_key_env"] = os.environ["VALIDATOR_KEY_ENV"] or None

with open(cfg_path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True)
print(f"    setup written to {cfg_path}: camera={os.environ['CAMERA_PORT']} "
      f"display={os.environ['DISPLAY_PORT']} vlm={os.environ['VLM_PROVIDER']} "
      f"validator={os.environ['VALIDATOR_PROVIDER']}")
PYEOF

chown -R "$PET_USER" "$PET_DIR"

echo "==> [7/7] systemd services"
sed -e "s|__PET_DIR__|$PET_DIR|g" \
    -e "s|__LLAMA_DIR__|$LLAMA_DIR|g" \
    -e "s|__MODEL__|$MODEL_DIR/$MODEL_GGUF|g" \
    -e "s|__MMPROJ__|$MODEL_DIR/$MMPROJ_GGUF|g" \
    -e "s|__USER__|$PET_USER|g" \
    "$REPO_DIR/pet/wavego-pet-vlm.service" > /etc/systemd/system/wavego-pet-vlm.service
sed -e "s|__PET_DIR__|$PET_DIR|g" \
    -e "s|__USER__|$PET_USER|g" \
    "$REPO_DIR/pet/wavego-pet.service" > /etc/systemd/system/wavego-pet.service

if [ "$TAKE_UART" -eq 1 ]; then
  echo "    --take-uart: stopping ugv_rpi app/agent services (link handover)"
  systemctl disable --now wavego.service wavego-agent.service 2>/dev/null || true
else
  echo "    NOTE: if the ugv_rpi web app or wavego-agent service is running,"
  echo "    it owns the ESP32 link. Stop it or re-run with --take-uart."
fi

systemctl daemon-reload
systemctl enable --now wavego-pet-vlm.service
systemctl enable wavego-pet.service

echo
echo "Done. Re-login once so the dialout/video group changes apply."
echo "Sanity checks:"
echo "  1. full hardware doctor:"
echo "     $PET_DIR/venv/bin/python -m wavego_pet.petserve --doctor"
echo "  2. vision smoke test:"
echo "     sudo $LLAMA_DIR/build/bin/llama-mtmd-cli \\"
echo "       -m  $MODEL_DIR/$MODEL_GGUF \\"
echo "       --mmproj $MODEL_DIR/$MMPROJ_GGUF \\"
echo "       --image /usr/share/icons/raspberry-pi.png \\"
echo "       -p 'Describe this image in one sentence.'"
echo "  3. start the pet:   sudo systemctl start wavego-pet"
echo "  4. watch it think:  journalctl -u wavego-pet -f"
