#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"
env_file="${repo_dir}/.env.production"
compose_files=(-f "${repo_dir}/compose.yaml" -f "${repo_dir}/compose.prod.yaml")

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: ./deploy/lightsail-deploy.sh [--pull]

Validates .env.production, optionally fast-forwards the Git checkout, builds
the production images, starts the stack, and verifies API/worker readiness.
EOF
  exit 0
fi

if [[ $# -gt 1 || ( $# -eq 1 && "${1}" != "--pull" ) ]]; then
  echo "Usage: ./deploy/lightsail-deploy.sh [--pull]" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Run: sudo ./deploy/install-docker-ubuntu.sh" >&2
  exit 1
fi

docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  else
    echo "Docker is installed but unavailable to this user." >&2
    exit 1
  fi
fi

if [[ ! -f "${env_file}" ]]; then
  install -m 0600 "${repo_dir}/.env.production.example" "${env_file}"
  if command -v openssl >/dev/null 2>&1; then
    generated_password="$(openssl rand -hex 32)"
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${generated_password}/" "${env_file}"
  fi
  echo "Created ${env_file}. Add the organiser gateway key and SiliconFlow key, then rerun this command." >&2
  exit 2
fi

chmod 0600 "${env_file}"

required_secret_names=(
  POSTGRES_PASSWORD
  LLM_GATEWAY_API_KEY
  QQFARM_SILICONFLOW_API_KEY
)
invalid_names=()
for name in "${required_secret_names[@]}"; do
  value="$(sed -n "s/^${name}=//p" "${env_file}" | tail -n 1)"
  if [[ -z "${value}" || "${value}" == replace-with-* ]]; then
    invalid_names+=("${name}")
  fi
done

if (( ${#invalid_names[@]} > 0 )); then
  printf 'Set these values in .env.production before deploying:\n' >&2
  printf '  - %s\n' "${invalid_names[@]}" >&2
  exit 2
fi

if [[ "${1:-}" == "--pull" ]]; then
  git -C "${repo_dir}" pull --ff-only
fi

compose=(
  "${docker_cmd[@]}" compose
  --project-directory "${repo_dir}"
  --env-file "${env_file}"
  "${compose_files[@]}"
)

"${compose[@]}" config --quiet
"${compose[@]}" up -d --build --wait --wait-timeout 600
"${compose[@]}" ps

if ! curl --fail --silent --show-error \
  'http://127.0.0.1/health/ready?require_worker=true'; then
  echo >&2
  echo "Readiness check failed. Recent service logs:" >&2
  "${compose[@]}" logs --tail=100 api worker web >&2
  exit 1
fi

echo
echo "QuoteWise is ready. Open http://<LIGHTSAIL_STATIC_IP>/ in a browser."
