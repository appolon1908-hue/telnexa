# Telnexa Jasmin SMS Gateway

Production-oriented Docker Compose deployment for `telnexa.co`. It provides Jasmin's HTTP/SMPP gateway, Redis, RabbitMQ, an HTTPS reverse proxy, signed webhook relay, host monitoring, health checks, persistence, and operator tooling. No carrier credentials or real routes are included.

The repository also contains an additive multi-tenant billing control plane: private PostgreSQL, decimal wallets, immutable ledger enforcement, atomic reservations, deterministic rate snapshots, simulator-safe charging, usage/margin data, invoice/payment foundations, tenant APIs, portal shells, signed middleware event outbox, migrations and billing backup/restore. See [billing architecture](docs/BILLING_ARCHITECTURE.md). Production SMS remains disabled until real provider credentials and an explicitly authorized destination are supplied.

## Architecture

```text
Customer / Codestra Middleware -> Telnexa commercial API -> durable dispatch worker
                                                        -> private Jasmin -> carrier
carrier -> Jasmin -> webhook relay -> Telnexa provider-event inbox
        -> billing/compliance/message state -> Middleware/customer outbox
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
# Set a real LETSENCRYPT_EMAIL, review domains, and replace every
# example.invalid image with an approved release @sha256 digest in .env
docker compose config
./scripts/start.sh
```

`generate-env.sh` replaces every `GENERATE_ME` value with an independent random secret, binds `SOURCE_SHA` to the clean checkout, and sets `.env` to mode 0600. `start.sh` rejects placeholder images, source drift, and local builds; it deploys only approved digest-addressed artifacts. Never deploy the public example values unchanged.

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

Non-secret configuration is versioned under `config/` and `docker/`; deployment secrets are installed as root-owned secret files. The private `telnexa-adapter` Jasmin user is created idempotently with bounded throughput. Middleware never receives those credentials.

SMPP account creation and updates require `Idempotency-Key` and `X-Correlation-ID`.
The durable identity is tenant + authenticated OIDC subject/API key + resource + action + API
version + key, and a changed semantic request is rejected with HTTP 409. Creation results contain
a one-time credential, so the exact result is encrypted at rest before it can be replayed; the
plaintext credential is never stored in the idempotency table.

Outbound callers use `https://api.telnexa.co/api/v1/messages` with tenant authentication, scope, idempotency key, and correlation ID. The Telnexa adapter alone maps the accepted message to Jasmin parameters and constructs the protected DLR URL at runtime. `https://sms.telnexa.co/send` is retired and returns 410.

Dedicated service accounts authenticate over TLS with HTTP Basic using the one-time `client_id` and `client_secret` returned by `POST /api/v1/service-accounts`, plus the required `X-Tenant-ID` header. Their database-backed scopes are enforced identically to API-key scopes; audit access requires `audit:read` or `admin`. Never place the Basic credential in a URL or log it.

Provider onboarding: [docs/ADDING_SMPP_PROVIDER.md](docs/ADDING_SMPP_PROVIDER.md). Customer onboarding: [docs/ADDING_SMS_CUSTOMER.md](docs/ADDING_SMS_CUSTOMER.md).

## Signed provider-event ingress

Jasmin sends inbound/DLR callbacks to the internal relay. The relay authenticates the source and posts only to Telnexa's private `/internal/v1/provider-events/jasmin` durable inbox. Telnexa resolves authoritative tenant/message state before emitting signed Middleware and customer events from its outbox.

Headers include `X-Signature-Version: v1`, `X-Telnexa-Timestamp`, `X-Telnexa-Event-Id`, and `X-Telnexa-Signature: sha256=<hex>`. Verify HMAC-SHA256 over the newline-joined canonical fields `v1`, uppercase HTTP method, normalized path, timestamp, event ID, source `telnexa`, and SHA-256 of the exact request body. Use constant-time comparison, reject timestamps older than five minutes, and deduplicate event IDs. Example bodies are in `examples/webhook-payloads.json`. Relay logs deliberately omit query strings, bodies, and secrets.

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

An API message remains queued while production SMS is disabled. A 426 from Nginx means TLS has not been installed. A 502 from the webhook relay means Telnexa's private provider-event inbox is unreachable; Jasmin must retry the callback.

## Upgrade procedure

Read upstream release and migration notes, run `scripts/backup.sh`, test on a clone, update one pinned major/minor image at a time, and run `docker compose config`, image builds, health/auth tests, persistence recreation, and a provider bind test. Use `scripts/update.sh` for ordinary fast-forward deployments. Never delete volumes during an upgrade unless a documented migration explicitly requires it.
