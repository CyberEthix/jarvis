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

# Idempotent 2026-2030 foundation bootstrap. This creates/updates the durable
# task, cognition graph, evidence, governance, memory, node, model, tool and
# evaluation structures before the UI starts. It also publishes the current
# readiness inventory into the Research Library.
python -m lisa_runtime.future_core --bootstrap

python -m lisa_runtime.cognition_app
