#!/bin/bash
# Agent stack setup: creates agent-env (virtualenv), installs the minimal
# dependency set, sanity-checks state_table.yaml, and self-tests the safety
# suites. Optionally installs the wavego-agent systemd service.
#
# Usage:
#   ./setup.sh                    venv + deps + config check + self-test
#   ./setup.sh --with-service     ...then install wavego-agent.service
#   ./setup.sh --check            verify an existing installation
#
# Run WITHOUT sudo (privileged steps call sudo internally).

set -u

if [ -n "$SUDO_USER" ] || [ -n "$SUDO_UID" ]; then
    echo "Run without sudo: ./setup.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SCRIPT_DIR/agent-env"
VENV_PY="$VENV_DIR/bin/python"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 not found"
python3 -c "import venv" 2>/dev/null || die "python3-venv missing (sudo apt install python3-venv python3-pip)"

[ -f "$REPO_ROOT/state_table.yaml" ] || die "state_table.yaml not found at $REPO_ROOT"

make_venv() {
    echo "-- creating virtualenv $VENV_DIR"
    python3 -m venv --system-site-packages "$VENV_DIR" || die "venv creation failed"
}

install_deps() {
    echo "-- installing dependencies (PyYAML, pyserial, fastapi, uvicorn, httpx)"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" || die "pip install failed"
}

check_config() {
    echo "-- sanity-checking state_table.yaml"
    "$VENV_PY" - "$REPO_ROOT/state_table.yaml" <<'EOF'
import sys
import yaml

with open(sys.argv[1]) as fh:
    cfg = yaml.safe_load(fh)

cb = cfg.get("motion", {}).get("codebook", {})
assert cb, "codebook is empty"
assert "000000" in cb, "HALT (000000) missing from codebook"
assert cb["000000"].get("verified") is True, "HALT must be verified"
for code, entry in cb.items():
    assert len(code) == 6, f"bad code length: {code}"
    int(code, 16)
    assert entry.get("esp32"), f"{code} has no esp32 mapping"
print(f"   codebook OK: {len(cb)} codes, HALT verified")
print(f"   esp32 port: {cfg.get('esp32', {}).get('port', '?')}")
print(f"   dashboard: 0.0.0.0:{cfg.get('dashboard', {}).get('port', 8000)}")
EOF
    [ $? -eq 0 ] || die "config sanity check failed"
}

self_test() {
    echo "-- self-test: validator + safety suites (simulate mode)"
    (cd "$REPO_ROOT" && "$VENV_PY" "$SCRIPT_DIR/test_validator.py" | tail -1)
    (cd "$REPO_ROOT" && "$VENV_PY" "$SCRIPT_DIR/test_safety.py" | tail -1)
}

do_setup() {
    if [ -x "$VENV_PY" ]; then
        echo "-- $VENV_DIR already exists, reusing (delete it to rebuild)"
    else
        make_venv
    fi
    install_deps
    check_config
    self_test
    echo ""
    echo "== setup complete."
    echo "== run:        $SCRIPT_DIR/run.sh --simulate   (dry run, no hardware)"
    echo "== run:        $SCRIPT_DIR/run.sh               (real hardware, token printed)"
    echo "== service:    $SCRIPT_DIR/install_agent.sh     (systemd autostart)"
}

do_check() {
    [ -x "$VENV_PY" ] || die "not installed: $VENV_PY not found (run ./setup.sh)"
    echo "-- venv: $VENV_DIR"
    "$VENV_PY" -c "import yaml, serial, fastapi, uvicorn; print('-- deps OK')"
    check_config
    echo "== installation looks good."
}

case "${1:-install}" in
    --check)        do_check ;;
    --with-service) do_setup && "$SCRIPT_DIR/install_agent.sh" ;;
    install)        do_setup ;;
    *) die "unknown option: $1 (use --check, --with-service or no arg)" ;;
esac
