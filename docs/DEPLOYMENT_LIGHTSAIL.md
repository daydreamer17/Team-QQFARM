# Lightsail Production Deployment

This runbook deploys QuoteWise as a single-host Docker Compose application on
an Ubuntu Amazon Lightsail instance. PostgreSQL and the FastAPI port remain
private; only the Nginx web entry point is exposed publicly.

## 1. Create the instance

1. Enter the NUS-ISS AWS sandbox and obtain an approved Hackathon Lease.
2. Open AWS CloudShell, clone this repository, and run the provisioning script:

   ```bash
   git clone https://github.com/daydreamer17/Team-QQFARM.git
   cd Team-QQFARM
   ./deploy/lightsail-provision.sh
   ```

   The script shows the exact region, image, RAM, and list price before asking
   for confirmation. It selects the least expensive active Linux plan with at
   least 4 GB RAM, creates an Ubuntu 24.04 instance in Singapore, attaches a
   static IPv4 address, and opens only HTTP/HTTPS in addition to the default
   SSH rule. Set `LIGHTSAIL_MIN_RAM_GB=8` when concurrent demo use warrants the
   larger plan.

3. Restrict TCP 22 to team IP addresses when possible. Do not open 5432, 8000,
   or 5173.

## 2. Install Docker

Connect with the Lightsail browser SSH client, clone the repository, and run
the included installer. It configures Docker's official Ubuntu repository and
installs Docker Engine, Buildx, and the Docker Compose plugin:

```bash
git clone https://github.com/daydreamer17/Team-QQFARM.git
cd Team-QQFARM
sudo ./deploy/install-docker-ubuntu.sh
```

Add the login user to the Docker group only if the team accepts the privilege
implications. Otherwise prefix the remaining Docker commands with `sudo`.

## 3. Clone and configure

Run `./deploy/lightsail-deploy.sh` once. On its first run it creates a private
`.env.production` file and generates the PostgreSQL password when OpenSSL is
available. The script stops before starting services so that secrets can be
added safely.

```bash
./deploy/lightsail-deploy.sh
nano .env.production
```

Edit `.env.production` on the server. Replace every `replace-with-*` value.
The organiser LLM key belongs in `LLM_GATEWAY_API_KEY`; it is not an AWS login
credential. Keep the existing SiliconFlow key for embeddings and reranking
unless the organiser gateway documents compatible endpoints for both.

## 4. Validate and start

```bash
./deploy/lightsail-deploy.sh
```

The first build downloads the Python, OCR, Node, Nginx, and PostgreSQL images
and can take several minutes.

## 5. Verify

```bash
docker compose \
  --env-file .env.production \
  -f compose.yaml \
  -f compose.prod.yaml \
  ps

docker compose \
  --env-file .env.production \
  -f compose.yaml \
  -f compose.prod.yaml \
  logs --tail=100 api worker web

curl -fsS 'http://127.0.0.1/health/ready?require_worker=true'
```

Open `http://STATIC_IP/` from a different network and run the complete Demo4
workflow. Confirm requirement parsing, quotation extraction, policy publishing,
compliance checks, comparison, the AI assistant, and report export.

## 6. Update

```bash
./deploy/lightsail-deploy.sh --pull
```

Do not use `docker compose down -v`; `-v` deletes the PostgreSQL and uploaded
file volumes. Take a Lightsail snapshot before a major update or migration.

## 7. HTTPS

The static-IP HTTP URL is sufficient for the first deployment check. For HTTPS,
point a domain at the static IP and terminate TLS with Caddy, Certbot, or a
Lightsail load balancer. Keep the application containers unchanged behind the
TLS proxy.
