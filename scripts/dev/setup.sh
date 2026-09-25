#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
ENV_FILE="$REPO_ROOT/.env"

command -v python3 >/dev/null 2>&1 || {
  echo "python3 was not found. Install Python 3.11 or newer." >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "npm was not found. Install Node.js 22, then run this script again." >&2
  exit 1
}

cd "$REPO_ROOT"

if [[ ! -x "$PYTHON" ]]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

echo "Installing Python dependencies..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -e '.[dev]'

echo "Installing frontend dependencies..."
(
  cd "$REPO_ROOT/frontend"
  npm ci
)

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_ROOT/.env.example" "$ENV_FILE"
  echo "Created .env from .env.example. Add model credentials before live-model tests."
fi

echo
echo "Setup complete. Start the project with:"
echo "  ./scripts/dev/start.sh"
