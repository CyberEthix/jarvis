#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [ ! -d .venv ]; then
  echo "Missing .venv. Create the Python 3.12 environment first."
  exit 1
fi
source .venv/bin/activate

if python -m pip --version >/dev/null 2>&1; then
  python -m pip install -r requirements-lisa-runtime.txt
elif command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r requirements-lisa-runtime.txt
else
  echo "Neither pip nor uv is available to install runtime dependencies."
  exit 1
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export LISA_OLLAMA_MODEL="${LISA_OLLAMA_MODEL:-gemma4:e2b}"
export LISA_UI_PORT="${LISA_UI_PORT:-8090}"
export LISA_OPEN_NOTEBOOK_URL="${LISA_OPEN_NOTEBOOK_URL:-http://127.0.0.1:5055}"
export LISA_IDLE_POLL_SECONDS="${LISA_IDLE_POLL_SECONDS:-15}"

OPEN_NOTEBOOK_ENV="${XDG_CONFIG_HOME:-$HOME/.config}/lisa/open-notebook.env"
if [ -f "$OPEN_NOTEBOOK_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$OPEN_NOTEBOOK_ENV"
  set +a
  export LISA_OPEN_NOTEBOOK_PASSWORD="${LISA_OPEN_NOTEBOOK_PASSWORD:-${OPEN_NOTEBOOK_PASSWORD:-}}"
fi

# Preserve incompatible legacy registry tables before creating the
# authoritative 2030 registry schema. This is idempotent and does not delete data.
python -m lisa_runtime.compat_migrate

# Idempotent 2026-2030 foundation bootstrap.
python -m lisa_runtime.future_core --bootstrap

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/lisa-runtime"
PID_FILE="$STATE_DIR/idle-loop.pid"
LOG_FILE="$STATE_DIR/idle-loop.log"
mkdir -p "$STATE_DIR"

IDLE_RUNNING=0
if [ -f "$PID_FILE" ]; then
  IDLE_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$IDLE_PID" ] && kill -0 "$IDLE_PID" 2>/dev/null; then
    IDLE_RUNNING=1
    echo "LISA idle research service already running (PID $IDLE_PID)."
  else
    rm -f "$PID_FILE"
  fi
fi

if [ "$IDLE_RUNNING" -eq 0 ]; then
  nohup python -m lisa_runtime.idle_loop_service >> "$LOG_FILE" 2>&1 &
  IDLE_PID=$!
  echo "$IDLE_PID" > "$PID_FILE"
  echo "Started LISA idle research service (PID $IDLE_PID)."
  echo "Idle service log: $LOG_FILE"
fi

# Notebook is the outer human workflow; cognition remains the mind-map layer.
python -m lisa_runtime.idle_app
