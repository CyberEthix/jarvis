#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [ ! -d .venv ]; then
  echo "Missing .venv. Create the Jarvis Python 3.12 environment first."
  exit 1
fi

source .venv/bin/activate
python -m pip install -r requirements-lisa-runtime.txt
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python -m lisa_runtime.desktop
