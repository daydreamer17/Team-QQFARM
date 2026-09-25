#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_FILE="$REPO_ROOT/.local-data/dev-processes.mac.env"
QUIET=false
STOP_POSTGRES=false

usage() {
  cat <<'EOF'
Usage: ./scripts/dev/stop.sh [--quiet] [--postgres]

  --quiet       Suppress normal status messages.
  --postgres    Also stop the PostgreSQL container. Data volumes are preserved.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet)
      QUIET=true
      shift
      ;;
    --postgres)
      STOP_POSTGRES=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

state_value() {
  awk -F= -v key="$1" '$1 == key { print $2; exit }' "$STATE_FILE"
}

stop_tree() {
  local pid="$1"
  local child
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" >/dev/null 2>&1 || return 0
  while read -r child; do
    [[ -n "$child" ]] && stop_tree "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -TERM "$pid" >/dev/null 2>&1 || true
}

force_tree() {
  local pid="$1"
  local child
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" >/dev/null 2>&1 || return 0
  while read -r child; do
    [[ -n "$child" ]] && force_tree "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -KILL "$pid" >/dev/null 2>&1 || true
}

if [[ -f "$STATE_FILE" ]]; then
  api_pid="$(state_value api_pid)"
  worker_pid="$(state_value worker_pid)"
  frontend_pid="$(state_value frontend_pid)"

  stop_tree "$frontend_pid"
  stop_tree "$worker_pid"
  stop_tree "$api_pid"

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$api_pid" >/dev/null 2>&1 \
      && ! kill -0 "$worker_pid" >/dev/null 2>&1 \
      && ! kill -0 "$frontend_pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done

  force_tree "$frontend_pid"
  force_tree "$worker_pid"
  force_tree "$api_pid"
  rm -f "$STATE_FILE"

  if [[ "$QUIET" != true ]]; then
    echo "API, Worker and frontend have been stopped."
  fi
elif [[ "$QUIET" != true ]]; then
  echo "No saved macOS API, Worker or frontend processes were found."
fi

if [[ "$STOP_POSTGRES" == true ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    (cd "$REPO_ROOT" && docker compose stop postgres)
    [[ "$QUIET" == true ]] || echo "PostgreSQL has been stopped; its data volume was preserved."
  elif [[ "$QUIET" != true ]]; then
    echo "Docker Desktop is not running; PostgreSQL was not changed."
  fi
fi
