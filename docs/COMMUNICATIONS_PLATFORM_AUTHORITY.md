# Communications Platform Authority — Telnexa / SMS

## Purpose

This document defines `appolon1908-hue/telnexa` as the principal SMS runtime/provider authority inside the unified communications platform.

## Permanent ownership

This repository owns:

- Jasmin SMS runtime and its restricted APIs;
- SMPP/HTTP provider connectivity;
- outbound SMS submission and provider routing;
- inbound MO messages;
- delivery receipts and failure callbacks;
- SMS-specific billing/rating/usage records owned by Telnexa;
- provider health, persistence, backups, monitoring and operations;
- the durable provider-event inbox, local state/compliance processing, and signed transactional outbox toward Middleware.

This repository does not own:

- voice/VICIdial — `Vicidialer-Codestra`;
- email/Postal/Mautic — `klyrow.com`;
- cross-system authorization, idempotency, durable command ledger or orchestration authority — `Middleware-`;
- public SDK/client contracts — `SDK-repository`;
- public gateway policy — `Kong`;
- identity issuance — `Keycloak`;
- business CRM state — `Odoo`;
- workflow authoring — `N8N`;
- shared edge TLS/routing — `Caddy`.

All repository references in this document are under `appolon1908-hue`. `Telnexa-web` is the separate public website, not an additional provider runtime.

## Required path

```text
Application / SDK
      -> Caddy
      -> Kong
      -> Keycloak-validated identity
      -> Middleware
      -> Telnexa commercial API
      -> Telnexa policy / billing / durable dispatch
      -> Jasmin
      -> SMPP carrier
```

Inbound and delivery events first pass through the authenticated callback relay into Telnexa's private `/internal/v1/provider-events/jasmin` durable inbox. Telnexa processes local message state, billing and STOP/HELP before transactional outbox delivery to Middleware. A direct Jasmin-to-Middleware relay cannot replace this local authority. Provider callbacks cannot select a tenant.

The local Telnexa portal retains its own accepted Keycloak issuer and `telnexa-api` audience. The separate Codestra machine trust uses `telnexa-gateway`, an explicit caller allowlist, tenant/account binding, and distinct `sms.send` / `sms.status.read` scopes. Source registration does not enable this trust or change either live realm.

## SMS command surface

Provider-neutral contracts should cover at minimum:

- send SMS;
- scheduled send request where platform policy permits;
- message-status lookup;
- provider/route health;
- sender identity validation;
- batch/bulk submission through bounded, quota-aware operations;
- cancellation only where the downstream provider contract can truthfully support it;
- reconciliation/read-back for indeterminate sends.

Effectful requests require tenant, service/actor identity, correlation ID, idempotency key, canonical request fingerprint and explicit SMS capability authorization. Proposed interfaces are not a claim of live deployment; generated OpenAPI and exact-source tests define the implemented API surface.

## SMS event surface

At minimum normalize and version:

- `sms.accepted`;
- `sms.submitted`;
- `sms.delivered`;
- `sms.failed`;
- `sms.received`;
- `sms.delivery_delayed` where supported;
- `sms.provider_rejected`;
- `sms.reconciled`.

Provider-specific DLR codes remain internal metadata unless intentionally promoted into a stable public schema. This is the desired public event surface; adapters must explicitly map current versioned provider events rather than silently rename persisted events.

## Consent, suppression and abuse

Before dispatch, Middleware must enforce applicable consent/suppression/policy rules. Telnexa remains the final local send gate and must enforce suppression, STOP/HELP state, provider-level safety, route restrictions, quotas and anti-abuse controls even during a Middleware outage. Provider acceptance does not override suppression or consent requirements.

## Safety rules

1. Production SMS remains separately activation-gated.
2. A merge never enables provider credentials, carrier routes or unrestricted destinations.
3. Unknown outcomes after possible provider acceptance remain indeterminate. Perform authoritative read-back; never blindly resubmit or fail over to another provider.
4. Signed callbacks require raw-body verification, timestamp tolerance and durable deduplication. Exact authenticated replay returns the existing acceptance; an altered payload reusing an event ID is rejected.
5. Carrier credentials, SMPP passwords, HMAC secrets, customer data and live route mappings never enter Git.
6. Jasmin management, Redis and RabbitMQ remain private.
7. Bulk sending requires explicit quotas/rate limits and tenant isolation.
8. Provider-local billing truth is reconciled with platform command state; Middleware must not invent delivery truth.
9. SMS cannot bypass Middleware authorization. Middleware cannot bypass the Telnexa commercial API with raw Jasmin credentials.
10. Emergency kill switches stop new external sends while retaining status, local suppression processing and reconciliation access.

## Cross-repository contract requirements

Changes affecting SMS require coordinated evidence from:

- `SDK-repository` — public/generated contract compatibility;
- `Kong` — route/scope/audience policy;
- `Keycloak` — machine identity/scopes/audiences when changed;
- `Middleware-` — command ledger, policy, adapter, suppression and reconciliation;
- `telnexa` — provider submission, callback and read-back behavior;
- `N8N` — workflow compatibility when consuming SMS events;
- `Odoo` — CRM/business mapping when applicable;
- `Caddy` — ingress/edge compatibility if routing changes.

## Release gates

Before live SMS activation:

1. exact-head and current merge-result CI are green in every affected repository, with resolved threads and fresh independent approval;
2. contracts and event schemas are semantically valid;
3. invalid identity/scope/tenant requests fail closed;
4. duplicate request handling is proven without duplicate SMS or billing;
5. callback tampering, expiry and altered replay are rejected, while exact replay is deduplicated;
6. provider acceptance, failure and unknown outcomes are tested;
7. read-back/reconciliation is proven;
8. suppression/consent and quota behavior is tested during dependency outages;
9. backup/restore and emergency disable are rehearsed;
10. explicit production activation approval is recorded separately from merge approval.

## Branching

Use short-lived `feature/*`, `fix/*`, `docs/*`, and `test/*` branches and promote through the repository's reviewed integration/release flow. Preserve protected-main immutable release policy. Documentation changes never authorize SMS activation, force pushes, or branch-protection changes.
