# Telnexa repository deployment report

Status: billing foundation implemented and locally validated; full SaaS portal mission remains blocked/incomplete as detailed below.

## Implemented

- Private PostgreSQL billing service, one-shot migration, API and durable middleware outbox worker as additive Docker services.
- Decimal wallet balances, row-locked reservations, append-only ledger with a PostgreSQL mutation-rejection trigger, idempotent simulator charging, releases, finalization and postpaid credit limits.
- Effective-dated provider/sell rate resolution, GSM-7/UCS-2 multipart calculation, rate snapshots, usage and gross-profit records.
- Invoice/payment data foundations, tenant-filtered wallet/ledger/message/invoice/usage APIs, Argon2 API keys, audit records, admin credits, portal/admin shells and OpenAPI.
- Billing database backup/restore integration, private Prometheus scrape, Nginx routes/security headers, generated billing secrets, and complete architecture/operations documentation set.

- Custom Jasmin 0.11.0 image with environment materialization, private jCli, HTTP API, SMPP server, DLR/MO daemons, idempotent API-user bootstrap, quotas, and persistent `/etc/jasmin` volume.
- Authenticated Redis with AOF, RabbitMQ AMQP broker, persistent volumes, health-gated dependencies, private internal networking, resource limits, log rotation, and restart policies.
- Nginx HTTP/HTTPS image with ACME webroot, TLS 1.2/1.3, HSTS after certificate installation, rate limiting, privacy-preserving access logs, and reserved `api.telnexa.co` endpoint.
- Certbot profile and DNS-validating TLS initialization script.
- HMAC-SHA256 webhook relay for inbound, DLR, and failed events with timestamp/replay guidance and secret-free logs.
- Private Prometheus/node-exporter monitoring and host/container health tooling.
- Startup, shutdown, restart, logs, health, backup, guarded restore, console, provider test, TLS, secret generation, and deployment update scripts.
- Provider/customer onboarding guides, credential templates, webhook payload examples, firewall guidance, backup/restore and upgrade procedures.

## Exposure model

Only ports 80/443 are published. Redis 6379, RabbitMQ 5672/15672, Jasmin HTTP 1401, jCli 8990, SMPP 2775, Prometheus 9090, node-exporter 9100, and webhook relay 8080 are Docker-internal. Future customer SMPP requires an explicit VPN or fixed-IP firewall decision.

## Required external actions

- Deploy to the target server and create `.env` with generated production credentials.
- Point `sms.telnexa.co` and `api.telnexa.co` A records to `37.27.128.39`; wait for propagation.
- Set a real Let's Encrypt operations email and run `scripts/tls-init.sh`.
- Supply real carrier host/port/system ID/password/bind/TON/NPI/TPS/DLR/sender policy values.
- Set the existing middleware HTTPS base URL and share the HMAC secret through its secret manager.
- Configure encrypted off-host backup storage and host firewall/SSH hardening.
- Supply an SSH identity accepted by `root@37.27.128.39` (or a least-privilege deployment account) so the live stack can be inventoried, backed up, deployed and tested.
- Confirm middleware's production Telnexa billing-event namespace/signing contract and provision its dedicated API/HMAC identity.
- Add DNS/SAN certificate coverage for `app.telnexa.co` and `admin.telnexa.co`.

No provider credentials are included, no production route is preconfigured, and no real SMS has been or will be sent during repository validation.

## Remaining implementation gaps

The mission's full definition of done is not claimed. Customer/admin shells still need complete login/logout/reset/MFA, CSRF and role workflows; interactive API/SMPP/sender/webhook/team management; invoices PDF/CSV and statements; payment webhook/refund flows; configurable quotas/TPS; pricing CSV tools; comprehensive admin analytics; notification templates; and the full required endpoint/security/tenant matrix. These are independent engineering items, but production deployment and integration evidence cannot proceed without host access. The current branch is suitable only as a reviewed billing foundation, not production activation.

## Validation results

Validated on 2026-08-15 using a separate `telnexa-validation` Compose project and loopback-only test web ports so the existing server deployment was not modified.

| Check | Result |
|---|---|
| Docker Compose parsing and service dependency graph | PASS |
| Clean custom image builds with secret-excluding `.dockerignore` | PASS |
| RabbitMQ, authenticated Redis, Jasmin, relay, Nginx, Prometheus health | PASS |
| Redis/RabbitMQ/Jasmin internal DNS connectivity | PASS |
| Only Nginx has published host ports | PASS |
| Redis, RabbitMQ, Jasmin jCli/API/SMPP, relay, and monitoring isolation | PASS |
| `unless-stopped` restart policy on all long-running services | PASS |
| Pre-TLS health endpoint and HTTP 426 API rejection | PASS |
| Missing and invalid Jasmin API authentication rejection | PASS |
| Valid API authentication with expected 412 no-route response | PASS; no SMS sent |
| Middleware group/user bootstrap and throughput quotas | PASS |
| Zero fake SMPP connectors and zero production MT routes | PASS |
| Jasmin identity/config persistence after container recreation | PASS |
| Redis AOF value persistence after container recreation | PASS |
| Webhook relay health and safe 503 when middleware target is unset | PASS |
| HMAC signature unit test covering timestamp and exact raw body | PASS |
| Backup archives for repository/`.env`, Jasmin config, and Redis state | PASS |
| Shell/Python syntax, Compose config, and Git whitespace checks | PASS |
| Billing image build and isolated PostgreSQL migration | PASS |
| Billing unit/simulator suite | PASS — 10/10 |
| PostgreSQL ledger UPDATE rejection | PASS |
| Billing database restart persistence | PASS |
| Custom-format dump and isolated restore | PASS |
| Billing PostgreSQL public port exposure | PASS — none |

TLS issuance was not attempted because public DNS is not assumed. A physical server reboot and real provider bind/SMS require the target host and authorized carrier credentials; restart/recreation behavior was tested through Docker instead.
