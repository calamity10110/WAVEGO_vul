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
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --take-uart)  TAKE_UART=1 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

echo "==> [1/7] apt packages"
apt-get update -qq
apt-get install -y -qq \
  python3-venv python3-pip python3-picamera2 python3-opencv \
  git cmake build-essential curl
usermod -aG dialout,video "$PET_USER" || true

echo "==> [2/7] hardware checks"

echo "    [camera] (the Pi 5 has two MIPI connectors: CAM0/DSI0 and CAM1/DSI1)"
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
  echo "      - plug the camera into CAM0 or CAM1 (either works)"
  echo "      - re-seat the ribbon: metal contacts facing the board,"
  echo "        blue stiffener facing the latch; then re-run this installer"
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

echo "    [optional DSI LCD] (status screen on DSI0 or DSI1)"
DSI_DEV="$(ls /sys/class/drm 2>/dev/null | grep -m1 DSI || true)"
if [ -n "$DSI_DEV" ]; then
  echo "      DSI display detected: $DSI_DEV — installing status UI"
  apt-get install -y -qq python3-pygame
else
  echo "      no DSI display detected — status UI skipped."
  echo "      (add one to DISP0/DISP1 anytime, then re-run this installer)"
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
