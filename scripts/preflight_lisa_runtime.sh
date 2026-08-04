#!/usr/bin/env bash
set -Eeuo pipefail

MODE="check"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/lisa-runtime"
DB_PATH="$RUNTIME_DIR/lisa_runtime.db"
BACKUP_DIR="$RUNTIME_DIR/backups"
LOG_DIR="$RUNTIME_DIR/logs"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/preflight_lisa_runtime.sh [--check|--clean]

Modes:
  --check   Inspect the machine and report readiness. Makes no changes.
  --clean   Safely stop stale Jarvis/Lisa processes, back up the runtime
            database, remove disposable caches and stale lock files, then
            run all readiness checks.

The script intentionally does NOT:
  - remove Ollama models
  - delete the Python virtual environment
  - delete the Lisa SQLite database
  - uninstall packages
  - stop the Ollama service
EOF
}

case "${1:-}" in
  ""|--check) MODE="check" ;;
  --clean) MODE="clean" ;;
  -h|--help) usage; exit 0 ;;
  *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
esac

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
pass() { printf '\033[1;32m[PASS]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*"; }

failures=0
warnings=0

record_fail() { fail "$*"; failures=$((failures + 1)); }
record_warn() { warn "$*"; warnings=$((warnings + 1)); }

header() {
  echo
  echo "============================================================"
  echo " LISA Cognitive Runtime Preflight"
  echo " Mode: $MODE"
  echo " Host: $(hostname)"
  echo " Time: $(date --iso-8601=seconds)"
  echo " Repo: $REPO_ROOT"
  echo "============================================================"
}

show_matching_processes() {
  info "Checking for Jarvis/Lisa processes"
  local matches
  matches="$(pgrep -a -f 'python(3)? .*jarvis|python(3)? .*desktop_app|python(3)? .*lisa_runtime|run_lisa_runtime|run_desktop_app|run_linux\.sh' || true)"
  if [[ -n "$matches" ]]; then
    printf '%s\n' "$matches"
    return 0
  fi
  pass "No matching Jarvis/Lisa processes found"
  return 1
}

stop_matching_processes() {
  local patterns=(
    'python(3)? .*jarvis\.daemon'
    'python(3)? .*desktop_app'
    'python(3)? .*lisa_runtime'
    'run_lisa_runtime\.sh'
    'run_desktop_app\.sh'
    'run_linux\.sh'
  )

  info "Stopping only known Jarvis/Lisa runtime processes"
  local pattern pids
  for pattern in "${patterns[@]}"; do
    pids="$(pgrep -f "$pattern" || true)"
    [[ -z "$pids" ]] && continue

    while read -r pid; do
      [[ -z "$pid" || "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
      info "Sending SIGTERM to PID $pid: $(ps -p "$pid" -o args= 2>/dev/null || true)"
      kill -TERM "$pid" 2>/dev/null || true
    done <<< "$pids"
  done

  sleep 2

  for pattern in "${patterns[@]}"; do
    pids="$(pgrep -f "$pattern" || true)"
    [[ -z "$pids" ]] && continue

    while read -r pid; do
      [[ -z "$pid" || "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
      record_warn "PID $pid did not stop after SIGTERM; sending SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
    done <<< "$pids"
  done
}

backup_database() {
  if [[ ! -f "$DB_PATH" ]]; then
    info "No existing Lisa database to back up"
    return
  fi

  mkdir -p "$BACKUP_DIR"
  local backup="$BACKUP_DIR/lisa_runtime_${TIMESTAMP}.db"

  if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "$DB_PATH" ".backup '$backup'"; then
      pass "Database backup created: $backup"
    else
      record_fail "SQLite backup failed"
    fi
  else
    cp -p "$DB_PATH" "$backup"
    pass "Database copied to: $backup"
    record_warn "sqlite3 is not installed; used a file copy instead of SQLite online backup"
  fi
}

clean_disposable_files() {
  info "Removing disposable Python caches and stale runtime locks"

  find "$REPO_ROOT" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
  find "$REPO_ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  find "$REPO_ROOT" -maxdepth 3 -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true

  mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
  find "$RUNTIME_DIR" -maxdepth 2 -type f \( -name '*.lock' -o -name '*.pid' \) -delete 2>/dev/null || true

  if [[ -d "$LOG_DIR" ]]; then
    find "$LOG_DIR" -type f -name '*.log' -mtime +14 -delete 2>/dev/null || true
  fi

  pass "Disposable cache and stale lock cleanup completed"
}

check_command() {
  local command_name="$1"
  local required="${2:-required}"
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "$command_name found: $(command -v "$command_name")"
  elif [[ "$required" == "required" ]]; then
    record_fail "$command_name is not installed or not on PATH"
  else
    record_warn "$command_name is not installed"
  fi
}

check_python() {
  info "Checking Python environment"
  local python_bin=""

  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    python_bin="$REPO_ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  else
    record_fail "No Python interpreter found"
    return
  fi

  local version
  version="$($python_bin -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  info "Python interpreter: $python_bin"
  info "Python version: $version"

  if "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)
PY
  then
    pass "Python version is within the supported 3.10-3.12 range"
  else
    record_fail "Use Python 3.10-3.12 for this runtime; detected $version"
  fi

  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    if "$python_bin" -c 'import PySide6, requests' >/dev/null 2>&1; then
      pass "Core Lisa UI dependencies import successfully"
    else
      record_warn "Core dependencies are not fully installed in .venv yet"
    fi
  else
    record_warn "Repository virtual environment does not exist yet"
  fi
}

check_ollama() {
  info "Checking Ollama"
  if ! command -v ollama >/dev/null 2>&1; then
    record_fail "Ollama command is not installed"
    return
  fi

  pass "Ollama CLI found"

  if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    pass "Ollama API is responding on 127.0.0.1:11434"
    local model_count
    model_count="$(curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("models", [])))' 2>/dev/null || echo unknown)"
    info "Installed Ollama model count: $model_count"
  else
    record_fail "Ollama API is not responding on 127.0.0.1:11434"
  fi
}

check_ports() {
  info "Checking local ports"
  if command -v ss >/dev/null 2>&1; then
    local listeners
    listeners="$(ss -ltnp 2>/dev/null | grep -E ':(11434|5678|4222|8222)([[:space:]]|$)' || true)"
    if [[ -n "$listeners" ]]; then
      printf '%s\n' "$listeners"
    else
      info "No monitored ports are currently listening"
    fi
  else
    record_warn "ss command not available; skipped listening-port inspection"
  fi
}

check_resources() {
  info "Checking disk, memory and GPU"

  local available_kb available_gb
  available_kb="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
  available_gb=$((available_kb / 1024 / 1024))
  info "Free disk space near repository: ${available_gb} GB"
  if (( available_gb < 10 )); then
    record_warn "Less than 10 GB free disk space"
  else
    pass "Disk space is adequate"
  fi

  if command -v free >/dev/null 2>&1; then
    local available_mb
    available_mb="$(free -m | awk '/^Mem:/ {print $7}')"
    info "Currently available memory: ${available_mb} MB"
    if (( available_mb < 2048 )); then
      record_warn "Less than 2 GB memory is currently available"
    else
      pass "Available memory is adequate for preflight"
    fi
  fi

  if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader; then
      pass "NVIDIA driver and GPU are visible"
    else
      record_warn "nvidia-smi exists but could not query the GPU"
    fi
  else
    record_warn "nvidia-smi not found; GPU acceleration cannot be verified"
  fi
}

check_database() {
  info "Checking Lisa runtime storage"
  mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$BACKUP_DIR"
  if [[ -w "$RUNTIME_DIR" ]]; then
    pass "Runtime directory is writable: $RUNTIME_DIR"
  else
    record_fail "Runtime directory is not writable: $RUNTIME_DIR"
  fi

  if [[ -f "$DB_PATH" ]]; then
    info "Existing database: $DB_PATH ($(du -h "$DB_PATH" | awk '{print $1}'))"
    if command -v sqlite3 >/dev/null 2>&1; then
      local integrity
      integrity="$(sqlite3 "$DB_PATH" 'PRAGMA integrity_check;' 2>/dev/null || true)"
      if [[ "$integrity" == "ok" ]]; then
        pass "SQLite integrity check passed"
      else
        record_fail "SQLite integrity check failed: ${integrity:-no result}"
      fi
    else
      record_warn "sqlite3 is unavailable; skipped database integrity check"
    fi
  else
    info "No existing Lisa runtime database; it will be created on first launch"
  fi
}

check_repo() {
  info "Checking repository"
  if [[ -f "$REPO_ROOT/requirements-lisa-runtime.txt" && -f "$REPO_ROOT/scripts/run_lisa_runtime.sh" ]]; then
    pass "Lisa runtime files are present"
  else
    record_fail "Lisa runtime files are missing from this checkout"
  fi

  if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    info "Git branch: $(git -C "$REPO_ROOT" branch --show-current)"
    local changes
    changes="$(git -C "$REPO_ROOT" status --porcelain)"
    if [[ -n "$changes" ]]; then
      record_warn "Repository contains uncommitted changes"
      printf '%s\n' "$changes"
    else
      pass "Repository working tree is clean"
    fi
  else
    record_warn "This checkout is not currently recognized as a Git working tree"
  fi
}

header

if [[ "$MODE" == "clean" ]]; then
  show_matching_processes || true
  stop_matching_processes
  backup_database
  clean_disposable_files
  echo
fi

show_matching_processes || true
check_command curl
check_command git
check_command sqlite3 optional
check_command ss optional
check_repo
check_python
check_ollama
check_ports
check_resources
check_database

echo
echo "============================================================"
echo " Preflight summary"
echo " Failures: $failures"
echo " Warnings: $warnings"
echo "============================================================"

if (( failures > 0 )); then
  fail "System is not ready. Resolve the failures above before setup."
  exit 1
fi

if (( warnings > 0 )); then
  warn "Preflight passed with warnings. Review them before setup."
else
  pass "System is ready for Lisa Cognitive Runtime setup."
fi
