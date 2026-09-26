#!/usr/bin/env bash
set -Eeuo pipefail
export AWS_PAGER=""

region="${AWS_REGION:-ap-southeast-1}"
instance_name="${LIGHTSAIL_INSTANCE_NAME:-qqfarm-prod}"
static_ip_name="${LIGHTSAIL_STATIC_IP_NAME:-qqfarm-prod-ip}"
minimum_ram_gb="${LIGHTSAIL_MIN_RAM_GB:-4}"

if ! command -v aws >/dev/null 2>&1; then
  echo "AWS CLI is required. Run this script from AWS CloudShell." >&2
  exit 1
fi

aws sts get-caller-identity --output text >/dev/null

availability_zone="$(
  aws lightsail get-regions \
    --region "${region}" \
    --include-availability-zones \
    --query "regions[?name=='${region}'].availabilityZones[0].zoneName | [0]" \
    --output text
)"
if [[ -z "${availability_zone}" || "${availability_zone}" == "None" ]]; then
  echo "No Lightsail availability zone is available in ${region}." >&2
  exit 1
fi

blueprint_id="$(
  aws lightsail get-blueprints \
    --region "${region}" \
    --query "blueprints[?isActive && platform=='LINUX_UNIX' && blueprintId=='ubuntu_24_04'].blueprintId | [0]" \
    --output text
)"
if [[ -z "${blueprint_id}" || "${blueprint_id}" == "None" ]]; then
  echo "The active Ubuntu 24.04 Lightsail blueprint is unavailable in ${region}." >&2
  exit 1
fi

read -r bundle_id bundle_price bundle_ram < <(
  aws lightsail get-bundles \
    --region "${region}" \
    --query "sort_by(bundles[?isActive && ramSizeInGb>=\`${minimum_ram_gb}\` && contains(supportedPlatforms, 'LINUX_UNIX')], &price)[0].[bundleId,price,ramSizeInGb]" \
    --output text
)
if [[ -z "${bundle_id:-}" || "${bundle_id}" == "None" ]]; then
  echo "No active Linux bundle with at least ${minimum_ram_gb} GB RAM is available." >&2
  exit 1
fi

echo "Region:      ${region} (${availability_zone})"
echo "Instance:    ${instance_name}"
echo "Blueprint:   ${blueprint_id}"
echo "Plan:        ${bundle_id} (${bundle_ram} GB RAM, USD ${bundle_price}/month list price)"
echo "Static IP:   ${static_ip_name}"
read -r -p "Create these Lightsail resources? [y/N] " answer
if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
  echo "Cancelled; no resources were created."
  exit 0
fi

if aws lightsail get-instance \
  --region "${region}" \
  --instance-name "${instance_name}" >/dev/null 2>&1; then
  echo "Instance ${instance_name} already exists; keeping it."
else
  aws lightsail create-instances \
    --region "${region}" \
    --instance-names "${instance_name}" \
    --availability-zone "${availability_zone}" \
    --blueprint-id "${blueprint_id}" \
    --bundle-id "${bundle_id}" \
    --tags key=Project,value=Team-QQFARM key=TeamCode,value=PZ2MLTO0 \
    --output json >/dev/null
fi

echo "Waiting for ${instance_name} to enter the running state..."
for _ in $(seq 1 60); do
  state="$(
    aws lightsail get-instance-state \
      --region "${region}" \
      --instance-name "${instance_name}" \
      --query state.name \
      --output text
  )"
  if [[ "${state}" == "running" ]]; then
    break
  fi
  if [[ "${state}" == "terminated" || "${state}" == "shutting-down" ]]; then
    echo "Instance entered terminal state: ${state}" >&2
    exit 1
  fi
  sleep 5
done
if [[ "${state:-}" != "running" ]]; then
  echo "Timed out waiting for the instance; current state: ${state:-unknown}." >&2
  exit 1
fi

attached_to=""
if aws lightsail get-static-ip \
  --region "${region}" \
  --static-ip-name "${static_ip_name}" >/dev/null 2>&1; then
  echo "Static IP ${static_ip_name} already exists; keeping it."
  attached_to="$(
    aws lightsail get-static-ip \
      --region "${region}" \
      --static-ip-name "${static_ip_name}" \
      --query staticIp.attachedTo \
      --output text
  )"
else
  aws lightsail allocate-static-ip \
    --region "${region}" \
    --static-ip-name "${static_ip_name}" \
    --output json >/dev/null
fi

if [[ -n "${attached_to}" && "${attached_to}" != "None" && "${attached_to}" != "${instance_name}" ]]; then
  echo "Static IP ${static_ip_name} is attached to ${attached_to}; refusing to move it." >&2
  exit 1
fi
if [[ "${attached_to}" != "${instance_name}" ]]; then
  aws lightsail attach-static-ip \
    --region "${region}" \
    --static-ip-name "${static_ip_name}" \
    --instance-name "${instance_name}" \
    --output json >/dev/null
fi

for port in 80 443; do
  aws lightsail open-instance-public-ports \
    --region "${region}" \
    --instance-name "${instance_name}" \
    --port-info "fromPort=${port},toPort=${port},protocol=tcp" \
    --output json >/dev/null
done

static_ip="$(
  aws lightsail get-static-ip \
    --region "${region}" \
    --static-ip-name "${static_ip_name}" \
    --query staticIp.ipAddress \
    --output text
)"

echo "Lightsail infrastructure is ready."
echo "Static IP: ${static_ip}"
echo "Next: connect with the Lightsail browser SSH client and follow docs/DEPLOYMENT_LIGHTSAIL.md."
