#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
FRONTEND_ROOT="$REPO_ROOT/frontend"
ENV_FILE="$REPO_ROOT/.env"
STATE_DIR="$REPO_ROOT/.local-data"
STATE_FILE="$STATE_DIR/dev-processes.mac.env"
LOG_DIR="$REPO_ROOT/logs/dev"
API_PORT=8000
FRONTEND_PORT=5173

usage() {
  cat <<'EOF'
Usage: ./scripts/dev/start.sh [--api-port PORT] [--frontend-port PORT]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-port)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      API_PORT="$2"
      shift 2
      ;;
    --frontend-port)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      FRONTEND_PORT="$2"
      shift 2
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

for port in "$API_PORT" "$FRONTEND_PORT"; do
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    echo "Invalid port: $port" >&2
    exit 2
  fi
done

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

process_alive() {
  [[ "$1" =~ ^[0-9]+$ ]] && kill -0 "$1" >/dev/null 2>&1
}

state_value() {
  awk -F= -v key="$1" '$1 == key { print $2; exit }' "$STATE_FILE"
}

wait_http() {
  local url="$1"
  local attempts="${2:-60}"
  local count=0
  while (( count < attempts )); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
    count=$((count + 1))
  done
  return 1
}

start_logged() {
  local name="$1"
  local working_directory="$2"
  shift 2
  (
    cd "$working_directory"
    exec nohup "$@" >"$LOG_DIR/$name.out.log" 2>"$LOG_DIR/$name.err.log"
  ) &
  STARTED_PID=$!
}

fail_start() {
  echo "$1" >&2
  "$SCRIPT_DIR/stop.sh" --quiet >/dev/null 2>&1 || true
  exit 1
}

command -v docker >/dev/null 2>&1 || {
  echo "docker was not found. Install and start Docker Desktop." >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "npm was not found. Run ./scripts/dev/setup.sh after installing Node.js 22." >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "curl was not found." >&2
  exit 1
}
command -v lsof >/dev/null 2>&1 || {
  echo "lsof was not found." >&2
  exit 1
}

[[ -x "$PYTHON" ]] || {
  echo "Python environment is missing. Run ./scripts/dev/setup.sh first." >&2
  exit 1
}
[[ -d "$FRONTEND_ROOT/node_modules" ]] || {
  echo "Frontend dependencies are missing. Run ./scripts/dev/setup.sh first." >&2
  exit 1
}
[[ -f "$ENV_FILE" ]] || {
  echo ".env is missing. Run ./scripts/dev/setup.sh first, then review it." >&2
  exit 1
}

if [[ -f "$STATE_FILE" ]]; then
  saved_api_pid="$(state_value api_pid)"
  saved_worker_pid="$(state_value worker_pid)"
  saved_frontend_pid="$(state_value frontend_pid)"
  if process_alive "$saved_api_pid" \
    && process_alive "$saved_worker_pid" \
    && process_alive "$saved_frontend_pid" \
    && port_in_use "$API_PORT" \
    && port_in_use "$FRONTEND_PORT"; then
    echo "API, Worker and frontend are already running."
    echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
    echo "API:      http://127.0.0.1:$API_PORT"
    exit 0
  fi
  "$SCRIPT_DIR/stop.sh" --quiet
fi

if port_in_use "$API_PORT"; then
  echo "API port $API_PORT is already in use." >&2
  exit 1
fi
if port_in_use "$FRONTEND_PORT"; then
  echo "Frontend port $FRONTEND_PORT is already in use." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start it, then run this script again." >&2
  exit 1
fi

mkdir -p "$STATE_DIR" "$LOG_DIR"

cd "$REPO_ROOT"
echo "Starting PostgreSQL..."
docker compose up -d --wait postgres

echo "Applying database migrations..."
"$PYTHON" -m dotenv -f "$ENV_FILE" run -- \
  "$PYTHON" -m alembic upgrade head

echo "Initialising checkpoints..."
"$PYTHON" -m dotenv -f "$ENV_FILE" run -- \
  "$PYTHON" -m supplier_comparison.checkpoints setup

echo "Starting API, Worker and frontend..."
start_logged api "$REPO_ROOT" \
  "$PYTHON" -m dotenv -f "$ENV_FILE" run -- \
  "$PYTHON" -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port "$API_PORT"
API_PID=$STARTED_PID

start_logged worker "$REPO_ROOT" \
  "$PYTHON" -m dotenv -f "$ENV_FILE" run -- \
  "$PYTHON" -m supplier_comparison.worker run-loop --poll-interval 1
WORKER_PID=$STARTED_PID

start_logged frontend "$FRONTEND_ROOT" \
  npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
FRONTEND_PID=$STARTED_PID

cat >"$STATE_FILE" <<EOF
repo_root=$REPO_ROOT
api_pid=$API_PID
worker_pid=$WORKER_PID
frontend_pid=$FRONTEND_PID
api_port=$API_PORT
frontend_port=$FRONTEND_PORT
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

wait_http "http://127.0.0.1:$API_PORT/health/live" 60 \
  || fail_start "API did not become ready. Check logs/dev/api.err.log."
wait_http "http://127.0.0.1:$FRONTEND_PORT" 60 \
  || fail_start "Frontend did not become ready. Check logs/dev/frontend.err.log."
wait_http "http://127.0.0.1:$API_PORT/health/worker" 40 \
  || fail_start "Worker heartbeat was not detected. Check logs/dev/worker.err.log."

echo
echo "Development services started:"
echo "  Frontend  http://127.0.0.1:$FRONTEND_PORT"
echo "  API       http://127.0.0.1:$API_PORT"
echo "  Swagger   http://127.0.0.1:$API_PORT/docs"
echo "  Worker    heartbeat detected"
echo "  Logs      $LOG_DIR"
echo
echo "Stop application services with: ./scripts/dev/stop.sh"
