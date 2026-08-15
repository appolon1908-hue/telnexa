# Codex Mission — Telnexa Production Communications Platform

You have full autonomy inside the `appolon1908-hue/telnexa` repository and on the designated Telnexa deployment server to build, install, configure, secure, test, document, and deploy a production-grade communications platform for **telnexa.co**.

Your job is not to stop at a working Jasmin container. Deliver a complete, Dockerized, multi-tenant SMS platform with client dashboard, admin dashboard, authentication, billing foundations, routing, observability, simulated carrier testing, provider failover, and production deployment tooling.

Proceed continuously until the definition of done is satisfied. Do not stop after each command for approval. Diagnose ordinary failures, fix them, retest, and continue.

## Core architecture

Build a Docker-first production stack containing, as appropriate:

- Jasmin SMS Gateway
- Redis
- RabbitMQ
- PostgreSQL for Telnexa application data
- Telnexa API gateway / application backend
- Telnexa client dashboard
- Telnexa admin dashboard
- reverse proxy (Nginx or Traefik)
- TLS / Let's Encrypt support
- Prometheus
- Grafana
- exporters / health checks
- background workers
- webhook / event delivery workers
- simulated SMPP/SMSC test service
- backup / restore tooling

Use separate internal Docker networks where useful. Do not expose Redis, PostgreSQL, RabbitMQ, Jasmin management ports, Docker socket, or internal debug ports to the public internet.

## Domain plan

Prepare the platform for:

- `telnexa.co` — public website / portal landing
- `app.telnexa.co` — client dashboard
- `admin.telnexa.co` — admin dashboard
- `api.telnexa.co` — public Telnexa API
- `sms.telnexa.co` — optional SMS/API endpoint where appropriate
- `status.telnexa.co` — optional public status page

Do not assume DNS exists. Detect the deployment server public IP and produce the exact DNS records required. If DNS cannot be changed automatically, continue everything else and document the required records.

## 1. Multi-tenant customer model

Build a production multi-tenant model around Jasmin.

Each customer must have a tenant/account record with:

- account ID
- company name
- primary contact
- status
- plan
- balance / credit limit fields
- API credentials
- optional SMPP credentials
- sender IDs
- destination restrictions
- throughput limits
- daily/monthly message quotas
- allowed countries
- webhook configuration
- webhook signing secret
- billing profile
- created / updated timestamps
- suspension / termination states

Provide admin controls to create, edit, suspend, reactivate, and delete/soft-delete customer accounts safely.

Map Telnexa customer controls to Jasmin users/groups/quotas where appropriate.

## 2. Smart routing engine

Build a routing layer capable of:

- primary provider routing
- backup provider routing
- country-specific routing
- MCC/MNC or network-specific routing where data is available
- customer-specific routes
- sender-specific routes
- throughput-aware routes
- failover routes
- future least-cost routing
- future quality-based routing

Store route definitions in PostgreSQL or configuration owned by Telnexa, and translate/apply them to Jasmin safely.

Maintain a route audit history.

Do not silently change production routes without recording who/what changed them.

## 3. DLR and message event pipeline

Implement a durable message lifecycle store.

Every submitted message should have a Telnexa message ID and track at least:

- tenant/customer ID
- external client message ID
- Jasmin message ID where available
- provider connector
- route used
- sender
- destination
- destination country/network if known
- encoding
- segment count
- submission timestamp
- provider acceptance timestamp
- DLR timestamps
- final delivery status
- provider error/status codes
- pricing/rate metadata
- retry count
- webhook delivery state

Handle:

- submitted
- accepted
- rejected
- queued
- sent
- delivered
- expired
- undeliverable
- failed
- unknown

Create idempotent DLR processing so duplicate callbacks do not create duplicate events.

## 4. Monitoring and observability

Deploy Prometheus and Grafana in Docker.

Create dashboards for:

- messages submitted per minute/hour/day
- successful vs failed messages
- DLR success rate
- delivery latency
- messages by customer
- messages by provider
- messages by destination country/network
- queue depth
- connector state
- SMPP bind state
- provider throughput
- HTTP API latency
- webhook failures/retries
- Redis health
- RabbitMQ health
- PostgreSQL health
- CPU
- RAM
- disk
- container health/restarts

Add alert-ready metrics for:

- provider disconnected
- queue growing abnormally
- disk space low
- memory pressure
- DLR failure spike
- API error spike
- webhook backlog

## 5. Provider health and automatic failover

Build provider health scoring and failover logic.

Track at minimum:

- connector connected/disconnected
- bind state
- recent submit success rate
- recent error rate
- recent DLR success rate
- recent delivery latency
- throttling responses
- timeout rate

Support configurable thresholds.

If the primary provider is unhealthy, route new traffic to an approved backup route according to policy.

Do not create routing loops.

Provide an admin control to manually disable or drain a provider.

## 6. Message policy and interceptor layer

Implement a policy layer around Jasmin interceptors or Telnexa API processing for:

- E.164 normalization
- TON/NPI normalization
- sender ID validation
- country allow/deny rules
- customer allow/deny rules
- max message length controls
- multipart controls
- Unicode handling
- mandatory DLR policy
- duplicate request protection / idempotency
- per-customer rate limiting
- prohibited sender patterns
- quiet-hours support where configured
- compliance metadata fields

Do not build mechanisms intended to bypass carrier, legal, anti-spam, or platform restrictions.

## 7. Prepaid billing and pricing foundation

Build a rate-card and balance system suitable for later commercial use.

Support:

- provider cost rate
- customer sell rate
- country/network rates
- customer-specific pricing
- markup rules
- effective date ranges
- currency field
- per-segment charging
- message cost estimation before send
- actual charge after accepted submission according to configured policy
- balance
- reserved balance if needed
- credits/debits
- transaction ledger
- manual adjustments with audit log
- low-balance threshold
- account suspension at configured limits

Never use floating-point arithmetic for money. Use integer minor units or fixed-precision decimals.

Do not charge twice for idempotent/retried requests.

## 8. Telnexa API gateway

Do not expose raw Jasmin as the primary commercial API.

Build a versioned Telnexa API, beginning with `/v1`.

Implement at least:

- `POST /v1/messages`
- `GET /v1/messages/:id`
- `GET /v1/messages`
- `POST /v1/messages/batch` if safely implemented
- `GET /v1/balance`
- `GET /v1/senders`
- `GET /v1/rates` for authorized customers if enabled
- webhook management endpoints
- API key management endpoints
- health endpoint

Requirements:

- API keys stored hashed where possible
- key prefix / last-four style display
- rotation
- revocation
- scopes/permissions
- per-key rate limits
- idempotency keys for send requests
- structured errors
- request IDs / correlation IDs
- OpenAPI documentation
- secure CORS policy
- audit logging

## 9. SMPP reseller interface

Prepare Jasmin/Telnexa for customers that connect over SMPP.

Support documented provisioning for:

- bind transmitter
- bind receiver
- bind transceiver
- submit_sm
- deliver_sm
- DLRs
- multipart SMS
- Unicode
- throughput limits
- max bind limits
- credentials
- IP allowlisting option

Create admin workflows for generating or resetting SMPP credentials without exposing secrets after initial creation.

## 10. Persistence, backups, and disaster recovery

Configure durable persistence for every stateful service.

Back up at minimum:

- PostgreSQL
- Jasmin persisted configuration
- application configuration
- route/rate configuration
- Grafana provisioning where necessary
- reverse proxy configuration

Do not commit secrets to Git.

Create automated backup scripts and restore scripts.

Document retention and test restore to a clean local/test environment where possible.

Create a disaster recovery runbook.

## Simulated SMPP/SMSC lab

While real provider approval is pending, build a Dockerized test SMSC/provider simulator or use a suitable open-source simulator.

The lab must allow Telnexa to test without sending real SMS:

- successful bind
- failed bind
- submit success
- submit reject
- throttling
- timeout
- connector drop
- reconnect
- DLR delivered
- DLR failed
- delayed DLR
- duplicate DLR
- MO/inbound SMS
- multipart SMS
- Unicode SMS
- provider outage
- failover to backup simulated provider

Never claim a real mobile message was delivered when only the simulator was used.

## Client dashboard

Build a polished responsive client portal at `app.telnexa.co`.

Required authentication features:

- login
- logout
- forgot password
- reset password
- session expiration
- secure cookies
- CSRF protection where applicable
- brute-force/rate-limit protections
- optional TOTP MFA foundation

Passwords must be hashed with Argon2id or another modern password hashing scheme.

Do not store plaintext passwords.

Client dashboard pages/features:

- overview dashboard
- current balance
- usage today / month
- delivery rate
- recent messages
- message search/filter
- message detail / timeline
- API keys
- create/revoke/rotate API key
- webhook settings
- webhook secret rotation
- webhook delivery logs
- sender IDs
- SMPP credentials/status if enabled
- rate card / pricing view if enabled
- billing transactions
- account/profile settings
- team users if implemented
- logout

Do not display message bodies to users without appropriate authorization and tenant isolation.

## Admin dashboard

Build a secure admin portal at `admin.telnexa.co`.

Create an initial admin bootstrap process. Do not hard-code an admin password in Git.

Preferred bootstrap behavior:

- require `ADMIN_EMAIL` and one-time `ADMIN_BOOTSTRAP_TOKEN` or equivalent secure environment values
- create first admin only if no admin exists
- force password setup/change on first login
- expire/disable the bootstrap token after success

Admin roles should support at least:

- superadmin
- operations
- support/read-only
- billing

Use RBAC and enforce authorization server-side.

Admin dashboard features:

- login/logout
- user/account management
- create client
- suspend/reactivate client
- reset/rotate client API keys
- set quotas
- set throughput
- configure sender restrictions
- provider connector overview
- provider health
- manually enable/disable/drain provider
- routes
- failover policy
- rate cards
- balances
- credit/debit adjustments
- message search
- message detail
- DLR timeline
- webhook failures
- system health
- queue status
- audit logs
- security logs
- configuration status

Every privileged admin action must create an audit event.

## Tenant isolation

Enforce strict tenant isolation at API and database levels.

A client must never be able to:

- access another client's messages
- access another client's API keys
- access another client's balance
- access another client's webhooks
- access another client's SMPP credentials
- access admin endpoints

Add automated tests specifically for cross-tenant access attempts.

## Security requirements

Implement and test:

- HTTPS
- secure cookies
- HSTS when appropriate
- CSP where appropriate
- CSRF protection
- SQL injection protections through parameterized queries/ORM
- XSS protections
- SSRF protections on webhook URLs
- webhook destination validation
- webhook HMAC signing
- timestamp + replay protection
- API rate limiting
- login rate limiting
- password reset token expiration
- secret rotation
- least-privilege Docker networking
- firewall documentation
- no public Redis/Postgres/RabbitMQ
- no public Jasmin management console
- no Docker socket exposure
- log redaction
- dependency vulnerability scanning where available

Do not weaken protections to make tests pass.

## Webhooks

Implement signed customer webhooks for:

- message.accepted
- message.sent
- message.delivered
- message.failed
- message.expired
- inbound.received

Webhook requirements:

- HMAC signature
- timestamp
- event ID
- idempotency
- retries with exponential backoff
- maximum retry count
- dead-letter state
- manual resend from dashboard/admin
- delivery logs

Protect against arbitrary callback SSRF. Only allow `https://` by default and block localhost/private/link-local destinations unless explicitly approved by an administrator.

## Docker requirements

Everything feasible must be Dockerized.

Provide a production `docker-compose.yml` (or layered compose files) with:

- health checks
- restart policies
- persistent named volumes
- internal networks
- resource limits where supported
- environment-based secrets/config
- log rotation
- dependency ordering based on health, not only container start

Keep secrets in `.env` or a secure runtime secret mechanism, never in committed files.

## Deployment tooling

Create scripts under `scripts/` for:

- `bootstrap-server.sh`
- `deploy.sh`
- `update.sh`
- `start.sh`
- `stop.sh`
- `restart.sh`
- `status.sh`
- `logs.sh`
- `backup.sh`
- `restore.sh`
- `healthcheck.sh`
- `create-admin.sh` if still required by the final design
- `test-stack.sh`
- `test-smpp-simulator.sh`

Scripts must be idempotent where practical.

Do not wipe existing unrelated server data.

## CI checks

Add GitHub Actions where useful for:

- lint
- unit tests
- API tests
- frontend tests
- Docker build validation
- compose validation
- dependency/security checks

Do not place production secrets into GitHub Actions workflows.

## Test plan

Do not mark this mission complete because containers merely start.

Create and run automated tests covering at minimum:

### Authentication

- valid login
- invalid login
- logout
- expired session
- password reset
- unauthorized admin access rejected
- tenant A cannot access tenant B
- suspended account cannot send

### API

- valid API key
- invalid/revoked API key
- rate limiting
- idempotent send
- duplicate idempotency key
- validation errors
- Unicode
- multipart SMS

### SMPP

- bind success
- bad credentials
- throughput limit
- submit success using simulator
- submit reject
- connection drop/reconnect

### Routing

- primary route
- backup route
- country route
- disabled provider
- automatic failover
- route recovery

### DLR/event processing

- delivered
- failed
- delayed
- duplicate DLR
- unknown message ID
- webhook retry
- webhook signature validation

### Billing

- cost calculation
- per-segment charging
- insufficient balance
- ledger correctness
- no duplicate charge on idempotent resend
- manual adjustment audit

### Dashboard

- client login/logout
- admin login/logout
- role restrictions
- API-key creation/revocation
- message search
- webhook configuration
- admin customer creation
- suspension/reactivation
- route/provider controls

### Infrastructure

- Redis private
- PostgreSQL private
- RabbitMQ private
- TLS configuration
- health checks
- restart policies
- backup
- restore
- container restart
- server reboot recovery where server access permits

## Production safety while waiting for carrier approval

Until real SMPP credentials are explicitly supplied:

- run all end-to-end SMS tests against the simulator
- do not invent provider credentials
- do not send real SMS
- do not claim real carrier delivery

When real credentials are later supplied, create a controlled smoke-test procedure that sends only to an explicitly authorized test number.

## Documentation

Create/update:

- `README.md`
- `ARCHITECTURE.md`
- `SECURITY.md`
- `DEPLOYMENT.md`
- `DISASTER_RECOVERY.md`
- `docs/ADDING_SMPP_PROVIDER.md`
- `docs/ADDING_SMS_CUSTOMER.md`
- `docs/API.md`
- `docs/WEBHOOKS.md`
- `docs/BILLING.md`
- `docs/ROUTING.md`
- `docs/OPERATIONS.md`
- `DEPLOYMENT_REPORT.md`

The deployment report must clearly separate:

- implemented
- tested successfully
- tested with simulator only
- blocked waiting for DNS
- blocked waiting for real SMPP credentials
- blocked waiting for any external approval

## Working rules

You have full autonomy to complete normal engineering and deployment work.

Do not stop for routine package installation, coding, Docker builds, migrations, configuration, or testing.

Diagnose and repair errors autonomously.

Before destructive changes to existing data/configuration, create a backup.

Do not wipe or repartition disks.

Do not modify unrelated servers/services.

Do not commit secrets.

Do not invent credentials.

Do not weaken authentication/security to make tests pass.

Do not implement spam-evasion, carrier-bypass, anti-abuse bypass, CAPTCHA bypass, or mechanisms designed to evade legal/compliance controls.

## Definition of done

This mission is complete only when:

1. Jasmin production stack runs in Docker.
2. Multi-tenant customer model works.
3. Smart routing and failover work against the simulator.
4. Durable DLR/event pipeline works.
5. Prometheus/Grafana monitoring works.
6. Provider health scoring and failover work.
7. Message policy/interceptor layer works.
8. Billing/rate-card foundation works.
9. Telnexa `/v1` API works with authentication and idempotency.
10. SMPP customer provisioning is implemented/tested against the simulator.
11. Backups and restore are implemented/tested.
12. Simulated SMPP provider lab works.
13. Client dashboard is complete, responsive, and tested.
14. Client login/logout/password reset works.
15. Admin dashboard is complete and RBAC-protected.
16. Secure initial admin bootstrap works without a hard-coded password.
17. Tenant isolation tests pass.
18. Webhook signing/retries/logs work.
19. Docker health/restart/persistence configuration works.
20. TLS/reverse-proxy configuration is ready or live when DNS permits.
21. CI checks pass.
22. Documentation is complete.
23. `DEPLOYMENT_REPORT.md` accurately states all remaining external blockers.

At the end, provide a concise completion report with:

- production URLs
- admin URL
- client URL
- API URL
- services/containers running
- test summary
- security summary
- simulator test results
- DNS status
- real SMPP/provider status
- anything still requiring human credentials or third-party approval

Proceed now and continue until completion.