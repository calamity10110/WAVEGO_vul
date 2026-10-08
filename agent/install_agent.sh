#!/bin/bash
# Agent stack installer: wavego-agent.service runs agent/serve.py, which hosts
# the agent loop (background thread) and the token-auth dashboard (uvicorn)
# sharing ONE Validator instance, so the ESP32 UART keeps a single owner.
# The dashboard token is stored root-only in /etc/wavego/agent.env.
#
# Usage:
#   ./install_agent.sh                install + enable + start (generates token)
#   ./install_agent.sh --token X      install with a chosen token
#   ./install_agent.sh --check        service status + token location
#   ./install_agent.sh --uninstall
#
# Run WITHOUT sudo (privileged steps call sudo internally).

set -u

if [ -n "$SUDO_USER" ] || [ -n "$SUDO_UID" ]; then
    echo "Run without sudo: ./install_agent.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_PY="$REPO_ROOT/ugv_rpi/ugv-env/bin/python"
UNIT_NAME="wavego-agent.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
ENV_DIR="/etc/wavego"
ENV_FILE="$ENV_DIR/agent.env"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$REPO_ROOT/state_table.yaml" ] || die "state_table.yaml not found at $REPO_ROOT"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY (run ugv_rpi/setup.sh first)"

write_unit() {
    echo "-- writing systemd unit $UNIT_PATH"
    sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=WAVEGO agent (validator + loop + dashboard, single UART owner)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_PY -m agent.serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}

install_service() {
    local token="${1:-}"
    if [ -z "$token" ]; then
        token="$(head -c 24 /dev/urandom | base64 | tr -d '=+/' | head -c 24)"
        echo "-- generated dashboard token: $token"
    fi

    echo "-- writing $ENV_FILE (root-only)"
    sudo mkdir -p "$ENV_DIR"
    if sudo grep -q "^AGENT_TOKEN=" "$ENV_FILE" 2>/dev/null; then
        echo "   token already present, keeping it (use --uninstall first to rotate)"
    else
        echo "AGENT_TOKEN=$token" | sudo tee "$ENV_FILE" >/dev/null
    fi
    sudo chmod 600 "$ENV_FILE"

    write_unit
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIT_NAME"
    echo ""
    echo "== installed. dashboard: http://<robot-ip>:8000  (token in $ENV_FILE)"
    echo "== logs: journalctl -u $UNIT_NAME -f =="
}

uninstall_service() {
    sudo systemctl disable --now "$UNIT_NAME" 2>/dev/null
    sudo rm -f "$UNIT_PATH"
    sudo systemctl daemon-reload
    echo "== removed $UNIT_NAME (token file $ENV_FILE kept; delete manually if desired)"
}

TOKEN_ARG=""
case "${1:-install}" in
    --check)
        systemctl status "$UNIT_NAME" --no-pager 2>/dev/null || echo "not installed"
        [ -f "$ENV_FILE" ] && echo "token file: $ENV_FILE"
        ;;
    --token)
        [ -n "${2:-}" ] || die "--token requires a value"
        TOKEN_ARG="$2"
        install_service "$TOKEN_ARG"
        ;;
    --uninstall) uninstall_service ;;
    install)     install_service "" ;;
    *) die "unknown option: $1" ;;
esac
