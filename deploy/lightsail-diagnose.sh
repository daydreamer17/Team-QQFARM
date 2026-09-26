#!/usr/bin/env bash
# Read-only runtime inspection plus ONE bounded synthetic gateway request.
set -Eeuo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  else
    echo "Docker unavailable even with sudo. No model request was sent." >&2
    exit 1
  fi
fi
compose=("${docker_cmd[@]}" compose --project-directory "${repo_dir}"
  --env-file "${repo_dir}/.env.production"
  -f "${repo_dir}/compose.yaml" -f "${repo_dir}/compose.prod.yaml")
"${compose[@]}" config --quiet
printf 'Checkout commit: '
git -C "${repo_dir}" rev-parse --short HEAD
"${compose[@]}" ps
for service in api worker; do
  printf '\nRunning %s configuration and code hashes:\n' "${service}"
  "${compose[@]}" exec -T "${service}" python - < "${repo_dir}/scripts/deployment_runtime_report.py"
done
printf '\nCheckout code + production configuration (one synthetic model call; no database writes):\n'
# Use checkout code explicitly. git pull alone does not rebuild the installed package.
"${compose[@]}" run --rm --no-deps -T \
  -v "${repo_dir}:/repo:ro" -e PYTHONPATH=/repo/src \
  worker python /repo/scripts/diagnose_quote_gateway.py
