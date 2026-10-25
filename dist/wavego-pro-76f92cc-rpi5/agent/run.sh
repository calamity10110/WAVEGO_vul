#!/bin/bash
# Run the WAVEGO agent (loop + dashboard, one shared validator, one UART owner).
#
# Usage:
#   ./run.sh                     hardware mode, token auto-generated and printed
#   ./run.sh --simulate          no hardware; dashboard + loop fully exercisable
#   ./run.sh --token MYTOKEN     chosen dashboard token (or AGENT_TOKEN env)
#   ./run.sh --port 8000         dashboard port (default: state_table dashboard.port)
#   ./run.sh --once              single agent cycle, no dashboard
#   ./run.sh --no-loop           dashboard only, agent loop off
#
# Ctrl-C: clean shutdown — loop stop, validator halt, port release.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PYTHON=""
for candidate in "$SCRIPT_DIR/agent-env/bin/python" "$REPO_ROOT/ugv_rpi/ugv-env/bin/python" "$(command -v python3)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || { echo "ERROR: no python found (run ./setup.sh)" >&2; exit 1; }

SIMULATE=""
TOKEN="${AGENT_TOKEN:-}"
PORT=""
HOST=""
ONCE=""
NO_LOOP=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --simulate) SIMULATE="--simulate"; shift ;;
        --token)    TOKEN="$2"; shift 2 ;;
        --port)     PORT="--port $2"; shift 2 ;;
        --host)     HOST="--host $2"; shift 2 ;;
        --once)     ONCE="--once"; shift ;;
        --no-loop)  NO_LOOP="1"; shift ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$TOKEN" ] && [ -z "$ONCE" ]; then
    TOKEN="$(head -c 24 /dev/urandom | base64 | tr -d '=+/' | head -c 20)"
    echo "== dashboard token: $TOKEN"
    echo "== (set AGENT_TOKEN or pass --token to choose your own)"
fi

ARGS="$SIMULATE $PORT $HOST $ONCE"
if [ -n "$ONCE" ]; then
    cd "$REPO_ROOT" && exec "$PYTHON" -m agent.loop $SIMULATE --once
elif [ -n "$NO_LOOP" ]; then
    echo "== starting dashboard only (simulate mode: hardware UART stays with the main app)"
    cd "$REPO_ROOT" && exec "$PYTHON" -m agent.dashboard ${TOKEN:+--token "$TOKEN"} $PORT $HOST --simulate
else
    cd "$REPO_ROOT" && exec "$PYTHON" -m agent.serve ${TOKEN:+--token "$TOKEN"} $ARGS
fi
