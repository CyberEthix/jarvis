#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

source .venv/bin/activate
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

python -m py_compile \
  src/lisa_runtime/models.py \
  src/lisa_runtime/repository.py \
  src/lisa_runtime/orchestrator.py \
  src/lisa_runtime/runtime_control.py \
  src/lisa_runtime/curiosity_manager.py \
  src/lisa_runtime/idle_loop_manager.py \
  src/lisa_runtime/idle_loop_service.py \
  src/lisa_runtime/idle_app.py \
  src/lisa_runtime/notebook_store.py \
  src/lisa_runtime/notebook_app.py

echo "Python compile check: PASS"
python -m lisa_runtime.notebook_doctor --json
