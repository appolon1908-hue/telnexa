# Telnexa Jasmin SMS Gateway

Production-oriented Docker Compose deployment for `telnexa.co`. It provides Jasmin's HTTP/SMPP gateway, Redis, RabbitMQ, an HTTPS reverse proxy, signed webhook relay, host monitoring, health checks, persistence, and operator tooling. No carrier credentials or real routes are included.

The repository also contains an additive multi-tenant billing control plane: private PostgreSQL, decimal wallets, immutable ledger enforcement, atomic reservations, deterministic rate snapshots, simulator-safe charging, usage/margin data, invoice/payment foundations, tenant APIs, portal shells, signed middleware event outbox, migrations and billing backup/restore. See [billing architecture](docs/BILLING_ARCHITECTURE.md). Production SMS remains disabled until real provider credentials and an explicitly authorized destination are supplied.

## Architecture

```text
Customer / Odoo / n8n
        | HTTPS
        v
existing middleware ---> sms.telnexa.co (Nginx) ---> Jasmin HTTP API
        ^                                             |
        | HMAC-signed MO/DLR/failure callbacks        | SMPP
        +---- webhook relay <---- Jasmin <-------------+---- carrier(s) ---- mobile network
```

RabbitMQ and Redis are attached only to Docker's internal `backend` network and have no host ports. Jasmin's HTTP API, management console, and SMPP server use Docker `expose` only. Nginx is the sole public application entry point on ports 80/443. The Jasmin container also has a controlled egress network for carrier connections. Prometheus and node-exporter remain internal.

Jasmin configuration and users/routes persist in `jasmin-config`; Redis uses AOF in `redis-data`; RabbitMQ and Prometheus have dedicated volumes. All services use `unless-stopped`, bounded logs, health checks, and resource limits so unrelated workloads cannot consume the full host.

## Requirements

- Linux host with Docker Engine 24+ and Docker Compose v2
- Recommended minimum: 8 vCPU, 16 GiB RAM, 50 GiB free disk
- Public TCP 80/443; outbound DNS, HTTPS, and carrier SMPP ports
- `curl`, `dig`, OpenSSL, Git, Bash, and `jq` for operator scripts
- DNS control for `telnexa.co`

## Installation

```bash
git clone https://github.com/appolon1908-hue/telnexa.git
cd telnexa
cp .env.example .env
./scripts/generate-env.sh
# Set a real LETSENCRYPT_EMAIL and review domains in .env
docker compose config
docker compose up -d --build
./scripts/health.sh
```

`generate-env.sh` replaces every `GENERATE_ME` value with an independent random secret and sets `.env` to mode 0600. Direct startup with placeholder secrets is rejected by the Jasmin container; never deploy the public example values unchanged.

## DNS and TLS

Create these records, initially with TTL 300:

| Type | Name | Value |
|---|---|---|
| A | `sms.telnexa.co` | `37.27.128.39` |
| A | `api.telnexa.co` | `37.27.128.39` |

Do not request a certificate until public DNS resolves to the deployment server. Before TLS, Nginx serves ACME and `/healthz` on HTTP but rejects API requests with 426. After DNS propagates and `.env` contains a real email:

```bash
./scripts/tls-init.sh
curl -fsS https://sms.telnexa.co/healthz
```

Certbot stores certificates in the `letsencrypt` volume. Renew with the same Certbot webroot command or schedule `docker compose --profile tls run --rm certbot renew && docker compose restart nginx` daily; Certbot only renews when required.

## Configuration and API

Non-secret configuration is versioned under `config/` and `docker/`; deployment secrets live only in `.env`. The initial `middleware` Jasmin group/user is created idempotently at startup with HTTP/SMPP throughput quotas. Retrieve its username/password from `.env` and place them in the middleware's secret manager.

After TLS, outbound middleware requests use `https://sms.telnexa.co/send`. Use URL/form parameters `username`, `password`, `to`, `from`, `content`, `coding`, `dlr`, `dlr-level`, `dlr-url`, and `dlr-method`. Use `coding=8` for Unicode. Request DLR callbacks through `http://webhook-relay:8080/events/dlr`; this URL is internal to Docker.

Provider onboarding: [docs/ADDING_SMPP_PROVIDER.md](docs/ADDING_SMPP_PROVIDER.md). Customer onboarding: [docs/ADDING_SMS_CUSTOMER.md](docs/ADDING_SMS_CUSTOMER.md).

## Signed middleware webhooks

Set `WEBHOOK_TARGET_BASE_URL=https://middleware.example` and rotate `WEBHOOK_HMAC_SECRET`. Jasmin sends inbound/DLR callbacks to the internal relay. The relay normalizes and posts to:

- `<base>/webhooks/sms/inbound`
- `<base>/webhooks/sms/dlr`
- `<base>/webhooks/sms/failed`

Headers are `X-Telnexa-Timestamp: <unix-seconds>` and `X-Telnexa-Signature: sha256=<hex>`. Verify `HMAC-SHA256(secret, timestamp + "." + raw_request_body)` with constant-time comparison, reject timestamps older than five minutes, and deduplicate message/event IDs. Example bodies are in `examples/webhook-payloads.json`. Relay logs deliberately omit query strings, bodies, and secrets.

## Operations

```bash
./scripts/start.sh                 # build/start and health check
./scripts/stop.sh                  # orderly stop; volumes retained
./scripts/restart.sh               # restart and validate
./scripts/logs.sh jasmin           # redacted application logs
./scripts/health.sh                # containers, dependencies, API, disk
./scripts/console.sh               # loopback jCli inside container
./scripts/backup.sh                # root-readable local backup
./scripts/restore.sh BACKUP_DIR    # requires CONFIRM_RESTORE=YES
./scripts/provider-test.sh ID      # inspect only; no SMS
./scripts/update.sh                # backup, fast-forward, rebuild, validate
```

Useful jCli commands: `smppccm -l`, `mtrouter -l`, `morouter -l`, `httpccm -l`, `group -l`, `user -l`, `stats --smppc`, `persist`, and `load`.

## Security and firewall

Allow only SSH and web traffic on the host:

```bash
ufw default deny incoming
ufw default allow outgoing
ufw limit 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'ACME and HTTPS redirect'
ufw allow 443/tcp comment 'Telnexa HTTPS API'
ufw enable
```

Use SSH keys, Fail2ban, unattended security updates, and encrypted off-host backups. Do not publish ports 5672, 6379, 8990, 1401, 2775, 9090, or 9100. Customer SMPP should use VPN or an explicit fixed-IP allowlist. Docker environment values are visible to root/Docker administrators; restrict Docker access equivalently to root.

## Backups and restore

`scripts/backup.sh` archives the repository configuration, `.env`, Jasmin configuration/store, and Redis state. Backups contain credentials: mode 0700/0600, encrypt them, and copy them off-host. Default retention guidance is 14 days; the script lists expired sets rather than deleting them automatically. RabbitMQ carries transient queues, not authoritative business records; drain or snapshot it separately when strict in-flight recovery is required.

Before restoring, take a new backup, stop traffic, verify archive checksums, and set `CONFIRM_RESTORE=YES`. The restore script replaces Jasmin/Redis volume contents and restarts the stack. Verify users, connectors, routes, API authentication, and DLR flow before reopening traffic.

## Monitoring and troubleshooting

Prometheus retains 15 days of node CPU, RAM, filesystem, and its own metrics. It is private; access it through an SSH tunnel or an authenticated monitoring network, never by publishing 9090 globally. `scripts/health.sh` checks every production container, Redis authentication, RabbitMQ, Jasmin `/ping`, and disk usage.

Common diagnostics:

```bash
docker compose ps
docker compose logs --tail=200 jasmin rabbitmq redis nginx webhook-relay
docker compose exec jasmin python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:1401/ping').read())"
docker network inspect telnexa_backend
df -h
```

An authenticated send returning `No route found` is expected before a real carrier route exists. A 426 from Nginx means TLS has not been installed. A 503 from the webhook relay means `WEBHOOK_TARGET_BASE_URL` is intentionally unset or unreachable.

## Upgrade procedure

Read upstream release and migration notes, run `scripts/backup.sh`, test on a clone, update one pinned major/minor image at a time, and run `docker compose config`, image builds, health/auth tests, persistence recreation, and a provider bind test. Use `scripts/update.sh` for ordinary fast-forward deployments. Never delete volumes during an upgrade unless a documented migration explicitly requires it.
