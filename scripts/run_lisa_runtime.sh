#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Create the Lisa Python 3.12 environment first."
  echo "Example: uv venv --python 3.12 .venv"
  exit 1
fi

source .venv/bin/activate

PYTHON_VERSION="$(python -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if ! python - <<'PY'
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)
PY
then
  echo "Unsupported Python version: $PYTHON_VERSION. Use Python 3.10-3.12."
  exit 1
fi

echo "Using Python $PYTHON_VERSION from $(command -v python)"

if python -m pip --version >/dev/null 2>&1; then
  python -m pip install -r requirements-lisa-runtime.txt
elif command -v uv >/dev/null 2>&1; then
  echo "pip is not installed in .venv; installing dependencies with uv."
  uv pip install --python .venv/bin/python -r requirements-lisa-runtime.txt
else
  echo "Neither pip nor uv is available."
  echo "Install pip with: python -m ensurepip --upgrade"
  exit 1
fi

python - <<'PY'
import PySide6
import requests
print("Lisa runtime dependencies: OK")
PY

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m lisa_runtime.desktop
