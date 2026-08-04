#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/lisa-runtime"
PID_FILE="$STATE_DIR/idle-loop.pid"
LOG_FILE="$STATE_DIR/idle-loop.log"

cd "$REPO_ROOT"
source .venv/bin/activate
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -f "$PID_FILE" ]; then
  IDLE_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
else
  IDLE_PID=""
fi

if [ -n "$IDLE_PID" ] && kill -0 "$IDLE_PID" 2>/dev/null; then
  echo "Idle research service: RUNNING (PID $IDLE_PID)"
else
  echo "Idle research service: STOPPED"
fi

if ss -ltn 2>/dev/null | grep -q ':8090 '; then
  echo "Notebook UI: LISTENING on 127.0.0.1:8090"
else
  echo "Notebook UI: NOT LISTENING on port 8090"
fi

python -m lisa_runtime.notebook_doctor

echo
if [ -f "$LOG_FILE" ]; then
  echo "Recent idle service log:"
  tail -n 20 "$LOG_FILE"
fi
