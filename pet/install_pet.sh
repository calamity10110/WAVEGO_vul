#!/bin/bash
# install_pet.sh — fresh Raspberry Pi 5 OS (Bookworm 64-bit) -> robot pet.
#
# What it does:
#   1. apt packages (picamera2 + OpenCV come from apt, never pip on Pi OS)
#   2. builds llama.cpp (CPU, native) into /opt/llama.cpp
#   3. downloads Qwen3-VL-2B-Instruct GGUF (~1.1 GB) + vision encoder
#   4. venv at /opt/wavego-pet/venv (--system-site-packages: apt OpenCV/camera)
#   5. installs this repo's pet/ + wavego_agent/ to /opt/wavego-pet
#   6. systemd services: wavego-pet-vlm (llama-server) + wavego-pet (petserve)
#
# Usage:  sudo ./install_pet.sh [--skip-build] [--take-uart]
#   --skip-build  reuse an existing /opt/llama.cpp build (re-runs are fast)
#   --take-uart   disable the ugv_rpi web app services so the pet owns the UART
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

echo "==> [1/6] apt packages"
apt-get update -qq
apt-get install -y -qq \
  python3-venv python3-pip python3-picamera2 python3-opencv \
  git cmake build-essential curl

echo "==> [2/6] llama.cpp (CPU build, native flags)"
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

echo "==> [3/6] model download ($MODEL_GGUF + $MMPROJ_GGUF)"
mkdir -p "$MODEL_DIR"
for f in "$MODEL_GGUF" "$MMPROJ_GGUF"; do
  if [ ! -s "$MODEL_DIR/$f" ]; then
    curl -fL --retry 3 -o "$MODEL_DIR/$f" "$HF_BASE/$f"
  else
    echo "    $f already present"
  fi
done

echo "==> [4/6] python venv (--system-site-packages: apt picamera2/cv2)"
mkdir -p "$PET_DIR"
python3 -m venv --system-site-packages "$PET_DIR/venv"
"$PET_DIR/venv/bin/pip" install --upgrade pip --quiet
"$PET_DIR/venv/bin/pip" install --quiet -r "$REPO_DIR/pet/requirements.txt"

echo "==> [5/6] deploy pet + wavego_agent"
mkdir -p "$PET_DIR"
cp -r "$REPO_DIR/pet/wavego_pet"      "$PET_DIR/"
cp -r "$REPO_DIR/wavego_agent"        "$PET_DIR/"
cp    "$REPO_DIR/pet/pet_config.yaml" "$PET_DIR/"
chown -R "$PET_USER" "$PET_DIR"

echo "==> [6/6] systemd services"
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
  echo "    --take-uart: stopping ugv_rpi app/agent services (UART handover)"
  systemctl disable --now wavego.service wavego-agent.service 2>/dev/null || true
else
  echo "    NOTE: if the ugv_rpi web app or wavego-agent service is running,"
  echo "    it owns the UART. Stop it or re-run with --take-uart."
fi

systemctl daemon-reload
systemctl enable --now wavego-pet-vlm.service
systemctl enable wavego-pet.service

echo
echo "Done. Sanity checks:"
echo "  1. vision smoke test:"
echo "     sudo $LLAMA_DIR/build/bin/llama-mtmd-cli \\"
echo "       -m  $MODEL_DIR/$MODEL_GGUF \\"
echo "       --mmproj $MODEL_DIR/$MMPROJ_GGUF \\"
echo "       --image /usr/share/icons/raspberry-pi.png \\"
echo "       -p 'Describe this image in one sentence.'"
echo "  2. start the pet:   sudo systemctl start wavego-pet"
echo "  3. watch it think:  journalctl -u wavego-pet -f"
