# Telnexa complete SaaS deployment report

Execution date: 2026-08-15 (Europe/Berlin)

Branch: `agent/complete-saas-platform`
Production safety posture: no real SMS sent; no carrier credentials invented; existing `/opt/telnexa`, Kyqra, Klyrow, middleware, n8n and Odoo data were not modified.

The supplied mission document is truncated at line 743 in the middle of §23 (customer webhook retries). Every requirement present in the repository was assessed; the missing remainder cannot be inferred safely.

## IMPLEMENTED

- Docker-first private commercial control plane: PostgreSQL, API, outbox worker, migration job, Redis/RabbitMQ/Jasmin integration topology, reverse proxy, Prometheus, Grafana provisioning, exporters and guarded backup/restore tooling.
- Tenant-owned wallets, reservations, fixed-precision rates, append-only ledger entries, immutable audit rows, invoice/payment foundations, usage/margin records, idempotent message charging and PostgreSQL row-level-security policies.
- GSM-7 extension/UCS-2 multipart accounting, effective-dated provider/sell rates, per-segment cost snapshots, atomic reserve/finalize/release and duplicate-charge protection.
- Scoped Argon2 API keys, request IDs, standard HTTP errors, OpenAPI, strict response headers, secure session cookies, login throttling, logout, password-reset token/session invalidation foundations and MFA-ready user records.
- Tenant-scoped messages/detail/timeline, balance, ledger, rates, invoices, invoice PDF, usage CSV, contacts/consent, senders, templates, campaigns, inbox, webhooks and finance summary APIs.
- Consent-source enforcement; STOP/HELP policy foundation; opted-out marketing suppression; unapproved-sender rejection; large/marketing campaign approval gates and disabled automatic dispatch.
- Durable MO/DLR records and deduplication, conversation keys, provider health/circuit state, route versions and dry-run failover preview.
- HMAC middleware outbox with event/correlation/idempotency headers, constant-time secret comparison at privileged boundaries, bounded exponential retry and DLQ state.
- Customer/admin responsive portal shells and developer OpenAPI. These expose the product information architecture but are not claimed as a complete interactive frontend.
- Existing production Jasmin stack remains healthy with private Redis, RabbitMQ and PostgreSQL; public carrier routes were not added.

## DEPLOYED

- Additive isolated Compose project `telnexa-saas`: `billing-db`, `billing-migrate`, `billing-api`, and `billing-worker`.
- Deployment uses private Docker networks and publishes no host ports. This prevents collision with the working `telnexa` project managed from `/opt/telnexa`.
- Production-like database migrations, RLS and immutability triggers are active in the isolated deployment.
- Not promoted behind public `api/app/admin.telnexa.co` routing because DNS/TLS/public reverse-proxy approval and production identity decisions remain external launch gates.

## TESTED/PASS

- Python suite: 18/18 passing, covering money/idempotency, reserve/release/finalize, prepaid/postpaid limits, GSM-7/UCS-2, rate specificity/margin, tenant filtering, invoices, payment uniqueness, consent/sender/campaign guardrails, MO STOP/HELP, DLR deduplication/timeline, failover, webhook secret display and secure-login throttling/cookies/logout.
- Compose schema/build/migration/API health/OpenAPI: PASS.
- Deployed simulator smoke: MT submission and settlement, Unicode, DLR `delivered`, MO `STOP`, suppression event and approved simulator route: PASS.
- PostgreSQL ledger mutation rejection: PASS.
- PostgreSQL RLS enabled for messages, contacts, ledger and audit (and all other tenant tables enumerated by migration): PASS.
- Network isolation: deployed PostgreSQL/API/worker have no published ports: PASS.
- Regression safety: existing Telnexa Jasmin and representative Kyqra/Klyrow health remained unchanged: PASS.
- HMAC webhook relay test, Jasmin credential/no-route behavior, Redis/Jasmin persistence and infrastructure checks inherited from the merged production stack remain documented and covered by repository tests/tooling.

## SIMULATOR-ONLY

- MT message acceptance, segment billing, reservations, settlement/release, message states and DLRs.
- MO/inbox, STOP/HELP, suppression, duplicate-MO/DLR handling and conversation association.
- Provider health, open-circuit exclusion, approved backup selection and route dry-run.
- Campaign creation/approval gating and portal/API flows. No campaign dispatcher sends real traffic.
- SMPP/customer/provider behavior has only simulator/Jasmin-local evidence; there is no approved upstream carrier bind.

## BLOCKED-EXTERNAL

- Carrier/provider credentials, approved routes, sender registrations, destination authorizations, throughput/bind limits and DLR/MO contracts were not supplied.
- Public DNS and certificate authorization for `api.telnexa.co`, `app.telnexa.co`, `admin.telnexa.co` (and optional monitoring hostname) are not established by this mission context.
- Middleware production API key/HMAC identity and its final event-schema acceptance are unavailable. Default private endpoint is `10.40.0.1`; no Odoo database write was attempted. <!-- gitleaks:allow - absence statement, not a credential -->
- Payment processor credentials/webhook signing secrets, tax/jurisdiction configuration, legal/compliance approval and invoice-number jurisdiction rules are unavailable.
- SMTP/email provider credentials are unavailable, so verification/reset tokens have durable foundations but no production mail delivery.
- KMS/secret-manager integration is unavailable; webhook secret plaintext is display-once and its production encrypted recovery/rotation path must use an approved KMS.
- Public reverse-proxy cutover cannot be done safely without deciding how the existing `/opt/telnexa` stack and shared ingress should route the new private service.
- The source mission is incomplete after line 743, so any requirements in the absent tail are an external specification blocker.

## MANUAL-LAUNCH-ACTIONS

1. Review and merge the production PR; back up `/opt/telnexa` and the isolated commercial database.
2. Provision a dedicated production secret set in the approved secret manager; rotate all validation `.env` values and configure a real Grafana password.
3. Approve the additive Compose project/service naming and connect only the shared ingress to `billing-api`; do not rename or replace the existing `telnexa` project in place.
4. Create/verify DNS, issue SAN certificates, add authenticated routes for API/client/admin, and confirm no internal database, broker, Jasmin management, simulator or monitoring ports are public.
5. Provision the dedicated middleware Telnexa identity; verify HMAC vectors, replay window, idempotency, retry/DLQ and Odoo/n8n mappings end to end.
6. Complete legal/compliance review per enabled country, configure STOP/HELP/quiet hours, approve sender IDs and load effective-dated sell/provider rates with minimum-margin review.
7. Supply approved carrier sandbox credentials first; run bind, MT, DLR, MO, multipart, Unicode, throttle and failover acceptance tests only to explicitly authorized destinations.
8. Configure SMTP and payment-provider sandboxes, then validate email verification/reset and signed payment lifecycle/refund/chargeback events without raw card storage.
9. Configure encrypted off-host backups, retention, alerting and a restore drill; perform a controlled restart/reboot test before traffic.
10. Obtain launch sign-off from operations, billing, security and compliance. Enable real routes and campaigns only after all gates pass; never reuse simulator credentials.
