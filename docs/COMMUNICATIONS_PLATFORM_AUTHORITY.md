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
- signed SMS event relay toward Middleware.

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

## Required path

```text
Application / SDK
      -> Caddy
      -> Kong
      -> Keycloak-validated identity
      -> Middleware
      -> Telnexa trusted adapter
      -> Telnexa / Jasmin
      -> SMPP carrier
```

Inbound and delivery events return through a private authenticated/signed boundary to Middleware and are normalized before becoming public platform events.

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

Effectful requests require tenant, service/actor identity, correlation ID, idempotency key, canonical request fingerprint and explicit SMS capability authorization.

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

Provider-specific DLR codes remain internal metadata unless intentionally promoted into a stable public schema.

## Consent, suppression and abuse

Before dispatch, Middleware must enforce applicable consent/suppression/policy rules. Telnexa must still enforce provider-level safety, route restrictions, quotas and anti-abuse controls. A provider accepting a message does not override platform suppression or consent requirements.

## Safety rules

1. Production SMS remains separately activation-gated.
2. A merge never enables provider credentials, carrier routes or unrestricted destinations.
3. Unknown outcomes after possible provider acceptance remain indeterminate and are reconciled before retry.
4. Signed callbacks must use raw-body verification, timestamp tolerance and replay protection.
5. Carrier credentials, SMPP passwords, HMAC secrets, customer data and live route mappings never enter Git.
6. Jasmin management, Redis and RabbitMQ remain private.
7. Bulk sending requires explicit quotas/rate limits and tenant isolation.
8. Provider-local billing truth is reconciled with platform command state; Middleware must not invent delivery truth.
9. SMS cannot be used as a bypass path around Middleware authorization.
10. Emergency kill switches must stop new external sends while retaining status and reconciliation access.

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

1. exact-head CI is green in every affected repository;
2. contracts and event schemas are semantically valid;
3. invalid identity/scope/tenant requests fail closed;
4. duplicate request handling is proven;
5. callback tampering, expiry and replay are rejected;
6. provider acceptance, failure and unknown outcomes are tested;
7. read-back/reconciliation is proven;
8. suppression/consent and quota behavior is tested;
9. backup/restore and emergency disable are rehearsed;
10. explicit production activation approval is recorded separately from merge approval.

## Branching

Use short-lived `feature/*`, `fix/*`, `docs/*`, and `test/*` branches and promote through the repository's reviewed integration/release flow. Documentation changes never authorize SMS activation.
