#!/bin/bash
# Host-side failsafe installer: wavego-failsafe.service watches the main app
# and sends a direct UART stop if the app dies within the motion grace window.
# No ESP32 reflash required; complements (does not replace) the documented
# manual safety rules.
#
# Usage:
#   ./install_failsafe.sh           install + enable + start
#   ./install_failsafe.sh --check   service status
#   ./install_failsafe.sh --uninstall
#
# Run WITHOUT sudo (privileged steps call sudo internally).

set -u

if [ -n "$SUDO_USER" ] || [ -n "$SUDO_UID" ]; then
    echo "Run without sudo: ./install_failsafe.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$SCRIPT_DIR/ugv-env/bin/python"
UNIT_NAME="wavego-failsafe.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$SCRIPT_DIR/failsafe_keepalive.py" ] || die "failsafe_keepalive.py not found"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY (run setup.sh first)"

install_service() {
    echo "-- writing systemd unit $UNIT_PATH"
    sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=WAVEGO Pro failsafe (post-mortem UART stop if the main app dies)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PY $SCRIPT_DIR/failsafe_keepalive.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT_NAME"
    echo ""
    echo "== installed. logs: journalctl -u $UNIT_NAME -f =="
}

uninstall_service() {
    sudo systemctl disable --now "$UNIT_NAME" 2>/dev/null
    sudo rm -f "$UNIT_PATH"
    sudo systemctl daemon-reload
    echo "== removed $UNIT_NAME"
}

case "${1:-install}" in
    --check)     systemctl status "$UNIT_NAME" --no-pager 2>/dev/null || echo "not installed" ;;
    --uninstall) uninstall_service ;;
    install)     install_service ;;
    *) die "unknown option: $1" ;;
esac
