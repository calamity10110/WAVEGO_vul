#!/bin/bash
# VLM control service installer: systemd unit for vlm_ctrl (Intern-Decision
# onboard engine) + dependency install + checkpoint sanity check.
#
# Usage:
#   ./install_vlm.sh                install deps + enable + start service
#   ./install_vlm.sh --check        service status + config sanity
#   ./install_vlm.sh --benchmark    on-robot latency benchmark (gate: 2.5 s/cycle)
#   ./install_vlm.sh --uninstall    stop + disable + remove unit
#
# Before installing: download the Intern-Decision-0.8B checkpoint and point
# fast_engine.checkpoint_dir in vlm_ctrl/config.yaml at it.
# Run WITHOUT sudo (privileged steps call sudo internally), like autorun.sh.

set -u

if [ -n "$SUDO_USER" ] || [ -n "$SUDO_UID" ]; then
    echo "Run without sudo: ./install_vlm.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$SCRIPT_DIR/ugv-env/bin/python"
UNIT_NAME="wavego-vlm.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$SCRIPT_DIR/vlm_ctrl/service.py" ] || die "vlm_ctrl/ not found next to this script"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY (run setup.sh first)"

CHECKPOINT=$("$VENV_PY" -c "import yaml,io;print(yaml.safe_load(io.open('$SCRIPT_DIR/vlm_ctrl/config.yaml',encoding='utf-8'))['fast_engine']['checkpoint_dir'])") \
    || die "cannot parse vlm_ctrl/config.yaml"

check_config() {
    echo "== config =="
    echo "checkpoint: $CHECKPOINT"
    if [ -d "$CHECKPOINT" ]; then
        echo "checkpoint dir: OK"
    else
        echo "checkpoint dir: MISSING"
        echo "  download Intern-Decision-0.8B and update fast_engine.checkpoint_dir:"
        echo "  https://huggingface.co/internlm/Intern-Decision-0.8B"
    fi
    "$VENV_PY" - <<'EOF'
import yaml, io, os
cfg = yaml.safe_load(io.open("vlm_ctrl/config.yaml", encoding="utf-8"))
req = ["api_base_robot", "frame_url", "service_port"]
missing = [k for k in req if k not in cfg]
print("required keys:", "OK" if not missing else f"MISSING {missing}")
EOF
}

run_benchmark() {
    cd "$SCRIPT_DIR" || die "cd failed"
    "$VENV_PY" -m vlm_ctrl.benchmark --cycles "${1:-10}"
}

install_service() {
    if [ ! -d "$CHECKPOINT" ]; then
        die "checkpoint missing at $CHECKPOINT (run --check for instructions)"
    fi

    echo "-- installing vlm dependencies into ugv-env"
    "$VENV_PY" -m pip install -r "$SCRIPT_DIR/vlm_ctrl/vlm_requirements.txt" \
        || die "pip install failed"

    if [ -f "$CHECKPOINT/requirements.txt" ]; then
        echo "-- installing model runtime requirements (watch for torch conflicts)"
        "$VENV_PY" -m pip install -r "$CHECKPOINT/requirements.txt" \
            || echo "   WARNING: model requirements failed - check torch/transformers versions"
    fi

    echo "-- writing systemd unit $UNIT_PATH"
    sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=WAVEGO Pro VLM goal control (onboard Intern-Decision)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PY -m vlm_ctrl.service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT_NAME"

    echo ""
    echo "== installed. next steps =="
    echo "  logs:        journalctl -u $UNIT_NAME -f"
    echo "  benchmark:   ./install_vlm.sh --benchmark   (must pass the 2.5 s gate)"
    echo "  note: VLM sessions and FOLLOW mode share CPU/RAM - avoid running both at once"
}

uninstall_service() {
    sudo systemctl disable --now "$UNIT_NAME" 2>/dev/null
    sudo rm -f "$UNIT_PATH"
    sudo systemctl daemon-reload
    echo "== removed $UNIT_NAME"
}

case "${1:-install}" in
    --check)     check_config; systemctl status "$UNIT_NAME" --no-pager 2>/dev/null || true ;;
    --benchmark) shift; run_benchmark "$@" ;;
    --uninstall) uninstall_service ;;
    install)     install_service ;;
    *) die "unknown option: $1" ;;
esac
