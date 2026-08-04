#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$REPO_ROOT/deploy/open-notebook/docker-compose.yml"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/lisa"
ENV_FILE="$CONFIG_DIR/open-notebook.env"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or is not on PATH."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required: docker compose version"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "OpenSSL is required to generate local secrets."
    exit 1
  fi
  umask 077
  cat > "$ENV_FILE" <<EOF
OPEN_NOTEBOOK_ENCRYPTION_KEY=$(openssl rand -hex 32)
OPEN_NOTEBOOK_PASSWORD=$(openssl rand -hex 24)
SURREAL_USER=lisa_notebook
SURREAL_PASSWORD=$(openssl rand -hex 24)
EOF
  chmod 600 "$ENV_FILE"
  echo "Created local sidecar credentials: $ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

echo
echo "LISA Open Notebook sidecar is starting."
echo "UI:  http://127.0.0.1:8502"
echo "API: http://127.0.0.1:5055"
echo "Credentials remain local in: $ENV_FILE"
echo
echo "For the existing Ollama service, configure the Open Notebook Ollama provider as:"
echo "  http://host.docker.internal:11434"
