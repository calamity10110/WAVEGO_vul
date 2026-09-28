#!/bin/bash
# Voice assistant installer: systemd service + speaker volume fix +
# autostart duplicate scanner (the field-notes mic failure was duplicate
# listen.py autostart entries fighting over the capture device).
#
# Usage:
#   ./install_voice.sh                 install + enable + start service
#   ./install_voice.sh --check-autostart   list duplicate/legacy autostart entries
#   ./install_voice.sh --clean-listen   remove listen.py entries from crontab
#   ./install_voice.sh --uninstall     stop + disable + remove unit
#
# Run WITHOUT sudo (privileged steps call sudo internally), like autorun.sh.

set -u

if [ -n "$SUDO_USER" ] || [ -n "$SUDO_UID" ]; then
    echo "Run without sudo: ./install_voice.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$SCRIPT_DIR/ugv-env/bin/python"
UNIT_NAME="wavego-voice.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
SPEAKER_CARD="${SPEAKER_CARD:-0}"
SPEAKER_VOLUME="${SPEAKER_VOLUME:-85%}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$SCRIPT_DIR/voice/voice_assistant.py" ] || die "voice/ not found next to this script"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY (run setup.sh first)"

check_autostart() {
    echo "== scanning for duplicate/legacy autostart entries =="
    local found=0

    echo "-- user crontab (@reboot entries)"
    crontab -l 2>/dev/null | grep -nE "listen\.py|voice_assistant|app\.py" && found=1

    echo "-- /etc/rc.local"
    if [ -f /etc/rc.local ]; then
        sudo grep -nE "listen\.py|voice_assistant" /etc/rc.local && found=1
    fi

    echo "-- systemd units"
    systemctl list-unit-files 2>/dev/null | grep -iE "listen|wavego-voice" && found=1

    echo "-- /etc/xdg/autostart"
    if [ -d /etc/xdg/autostart ]; then
        grep -rliE "listen\.py|voice_assistant" /etc/xdg/autostart 2>/dev/null && found=1
    fi

    if [ "$found" -eq 0 ]; then
        echo "no listen.py / duplicate entries found"
    else
        echo ""
        echo "NOTE: more than one entry touching listen.py or the mic will fight over"
        echo "the capture device (symptom: arecord 'device busy' / mic seems dead)."
        echo "Check ownership live with:  sudo fuser -v /dev/snd/*"
        echo "Remove legacy listen.py cron lines with:  ./install_voice.sh --clean-listen"
    fi
}

clean_listen_cron() {
    echo "-- removing listen.py lines from user crontab"
    if crontab -l 2>/dev/null | grep -q "listen\.py"; then
        crontab -l | grep -v "listen\.py" | crontab - && echo "removed" || die "crontab update failed"
    else
        echo "no listen.py entries in crontab"
    fi
}

write_unit() {
    echo "-- writing systemd unit $UNIT_PATH"
    sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=WAVEGO Pro voice assistant (persistent Moonshine ASR)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PY $SCRIPT_DIR/voice/voice_assistant.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
}

fix_volume() {
    echo "-- speaker volume: card $SPEAKER_CARD PCM -> $SPEAKER_VOLUME"
    if amixer -c "$SPEAKER_CARD" sset PCM "$SPEAKER_VOLUME" >/dev/null 2>&1; then
        sudo alsactl store 2>/dev/null || echo "   (alsactl store skipped)"
        echo "   saved"
    else
        echo "   WARNING: amixer failed - speaker may stay at boot default (30%)"
    fi
}

install_service() {
    echo "-- installing voice dependencies into ugv-env (may take a while)"
    "$VENV_PY" -m pip install -r "$SCRIPT_DIR/voice/voice_requirements.txt" \
        || die "pip install failed - check network / wheel availability for arm64"

    write_unit
    fix_volume
    check_autostart

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
    --check-autostart) check_autostart ;;
    --clean-listen)    clean_listen_cron ;;
    --uninstall)       uninstall_service ;;
    install)           install_service ;;
    *) die "unknown option: $1" ;;
esac
