#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/lisa-runtime"
PID_FILE="$STATE_DIR/idle-loop.pid"

cd "$REPO_ROOT"

if [ -f "$PID_FILE" ]; then
  IDLE_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$IDLE_PID" ] && kill -0 "$IDLE_PID" 2>/dev/null; then
    kill "$IDLE_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$IDLE_PID" 2>/dev/null; then
        break
      fi
      sleep 0.25
    done
  fi
  rm -f "$PID_FILE"
fi

pkill -f "python.*-m lisa_runtime.idle_app" 2>/dev/null || true
pkill -f "python.*-m lisa_runtime.notebook_app" 2>/dev/null || true
pkill -f "python.*-m lisa_runtime.cognition_app" 2>/dev/null || true
pkill -f "python.*-m lisa_runtime.nicegui_curiosity_app" 2>/dev/null || true

exec bash "$SCRIPT_DIR/run_lisa_workspace.sh"
