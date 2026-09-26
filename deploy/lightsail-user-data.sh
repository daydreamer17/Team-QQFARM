#!/usr/bin/env bash
set -Eeuo pipefail

exec > >(tee -a /var/log/qqfarm-bootstrap.log | logger -t qqfarm-bootstrap -s 2>/dev/console) 2>&1

repo_url="https://github.com/daydreamer17/Team-QQFARM.git"
repo_dir="/opt/Team-QQFARM"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes ca-certificates curl git

if [[ ! -d "${repo_dir}/.git" ]]; then
  if [[ -e "${repo_dir}" ]]; then
    echo "Refusing to replace unexpected path: ${repo_dir}" >&2
    exit 1
  fi
  git clone --branch main --single-branch "${repo_url}" "${repo_dir}"
fi

"${repo_dir}/deploy/install-docker-ubuntu.sh"
chown -R ubuntu:ubuntu "${repo_dir}"
install -m 0644 /dev/null /var/lib/qqfarm-bootstrap.complete

echo "QuoteWise host bootstrap completed successfully."
