# Telnexa ↔ Codestra Middleware Production SMS Integration Blueprint

## Purpose

This document extends the Telnexa production SMS adapter authority in PR #13 to cover the Server A / Codestra Middleware side of the production integration.

It is implementation authority, not production activation. External SMS delivery remains fail-closed until the provider adapter, Middleware integration, backups, isolated restore, security gates, and an explicitly authorized canary all pass.

## Hosts and ownership

| System | Public IP | Private IP | Responsibility |
|---|---:|---:|---|
| Codestra Control Plane / Server A | `65.109.65.169` | `10.40.0.1` | Caddy, Kong, Keycloak-facing integration, Codestra Middleware, Odoo/n8n business integration |
| Telnexa/Klyrow Communications | `37.27.128.39` | `10.40.0.4` | Telnexa commercial SMS control plane, Jasmin, SMPP/provider execution, Telnexa billing |
| VICIdial | `65.21.67.207` | `10.40.0.2` | Voice only; not an SMS provider target |

## Mandatory architecture

Outbound SMS must become:

```text
Client/Odoo/n8n/internal application
        |
        v
Caddy -> Kong -> Keycloak token validation
        |
        v
Codestra Middleware
        |
        | governed private Telnexa client
        v
Telnexa Commercial API 10.40.0.4
        |
        v
policy + consent + sender + route + billing reservation
        |
        v
durable dispatch job
        |
        v
Telnexa SMS dispatch worker
        |
        v
Jasmin adapter -> Jasmin -> carrier SMPP
```

Provider callback path:

```text
Carrier -> Jasmin -> Telnexa authenticated relay
        -> Telnexa durable provider-event inbox
        -> DLR/MO processor
        -> Telnexa authoritative message/compliance/billing state
        -> durable integration outbox
        -> Codestra Middleware receiver
        -> authorized Odoo/n8n/application processing
```

After migration the following path is forbidden:

```text
Codestra Middleware -> Jasmin raw /send
```

Middleware must not possess Jasmin user/password, SMPP provider credentials, carrier credentials, or a provider-specific DLR URL.

## Repository ownership

Codex must discover the live source before changes, but the expected ownership is:

- `Codestra-SRL/codestra-middleware`: Middleware connector/domain/application behavior.
- `appolon1908-hue/codestra-production-platform`: governed Server A deployment, Caddy/Kong/runtime configuration, release tuple and restricted operator controls.
- `appolon1908-hue/telnexa`: Telnexa commercial API, provider adapter, billing/routing/DLR/MO authority.

Do not duplicate the Telnexa provider adapter in Middleware.

## Middleware public API surface

Reuse an existing canonical SMS endpoint if already present. If no canonical endpoint exists, the preferred commercial surface is:

### `POST /v1/messages/sms`

Required authentication:

- Keycloak-issued bearer token validated at Kong/Middleware.
- Required permission/scope: `sms.send`.
- Tenant resolved server-side from the authenticated identity or authoritative tenant resolver.
- Never trust a browser-supplied tenant header as final authority.

Headers:

- `Authorization: Bearer ...`
- `Idempotency-Key: <opaque-client-key>` required.
- `X-Request-Id` optional; Middleware creates one if absent.
- `X-Correlation-Id` optional; Middleware creates one if absent.

Request model:

```json
{
  "to": "+15551234567",
  "sender": "ApprovedSender",
  "content": "Message text",
  "category": "transactional",
  "campaign_id": null,
  "metadata": {}
}
```

Rules:

- E.164 destination normalization.
- bounded message size.
- GSM-7/UCS-2 segment calculation may be previewed in Middleware but Telnexa remains final billing/segment authority.
- marketing category requires consent/compliance policy before Telnexa acceptance.
- sender must be an approved tenant sender in Telnexa.
- Middleware must not accept provider/connector selection from an ordinary customer request.

Accepted response:

```json
{
  "message_id": "uuid",
  "status": "accepted",
  "segments": 1,
  "encoding": "GSM-7",
  "correlation_id": "uuid"
}
```

Middleware must persist the Telnexa `message_id` and correlation identifiers for reconciliation.

### `GET /v1/messages/sms/{message_id}`

Returns the tenant-scoped Telnexa/Middleware view of message state. Middleware may cache metadata but Telnexa remains authoritative for provider submission and DLR state.

### `GET /v1/messages/sms/{message_id}/events`

Returns normalized event timeline after authorization/tenant checks.

## Internal Middleware -> Telnexa client contract

Middleware must call Telnexa over the approved private communications path, preferably mTLS plus a scoped service credential.

Preferred Telnexa endpoint:

`POST /api/v1/messages`

Required downstream service identity characteristics:

- dedicated client/service account for Codestra Middleware;
- audience restricted to Telnexa;
- scope `sms.send` only for ordinary send requests;
- short-lived credential/token;
- tenant authorization resolved from the trusted identity/context;
- no platform-admin scope;
- no Jasmin credential;
- no arbitrary provider-route scope.

If the existing Telnexa API currently requires `X-Tenant-ID`, Middleware may send the value only after deriving it from its authoritative tenant context. Telnexa must verify that value against the authenticated service authorization/tenant resolver and must not treat it as independent authority.

## Middleware database model

Reuse existing outbox/inbox/idempotency tables where equivalent. Do not create duplicates solely to match these names.

Required logical records:

### `sms_requests`

- `id`
- `tenant_id`
- `idempotency_key`
- `request_hash`
- `correlation_id`
- `telnexa_message_id`
- `destination_hash` or safely protected destination according to retention policy
- `sender`
- `category`
- `state`
- `created_at`
- `updated_at`

Unique: `(tenant_id, idempotency_key)`.

### `sms_provider_events_inbox`

- `event_id`
- `tenant_id`
- `message_id`
- `event_type`
- `schema_version`
- `payload_json`
- `state`
- `attempts`
- `next_attempt_at`
- `last_error`
- `received_at`

Unique event ID/replay key.

### `sms_integration_outbox`

May be the platform-wide outbox table. Required fields:

- tenant
- event type
- aggregate/message ID
- correlation ID
- idempotency key
- payload
- destination (`ODOO`, `N8N`, internal subscriber)
- state
- attempt/retry/DLQ metadata

### `sms_reconciliation_runs`

- `id`
- `message_id`
- `tenant_id`
- `middleware_state`
- `telnexa_state`
- `billing_state_summary`
- `drift_code`
- `resolution`
- timestamps

## Middleware request state machine

Preferred normalized states:

```text
RECEIVED
  -> AUTHORIZED
  -> TELNEXA_ACCEPTED
  -> PROVIDER_QUEUED
  -> SUBMITTED
  -> DELIVERED

terminal alternatives:
  DENIED
  FAILED
  EXPIRED
  UNDELIVERABLE
  CANCELLED

uncertain:
  SUBMISSION_UNKNOWN
```

Middleware must not invent `DELIVERED`; only authoritative Telnexa/provider events can produce that state.

## Idempotency

For `POST /v1/messages/sms`:

1. hash canonicalized request body plus immutable business dimensions;
2. key scope is tenant + Idempotency-Key;
3. same key + same hash returns original logical result;
4. same key + different hash returns `409`;
5. retrying Middleware -> Telnexa uses the same downstream idempotency key/correlation reference;
6. timeout after ambiguous provider submission must never cause Middleware to call a different provider itself.

Expected negative result:

`ALTERED_IDEMPOTENCY_REPLAY=DENIED`.

## Telnexa event receiver on Server A

Preferred internal receiver:

`POST /v1/internal/events/telnexa/sms`

If a canonical protected webhook path already exists, extend it instead of creating a duplicate.

Transport requirements:

- private network only or Caddy/Kong protected private route;
- mTLS where the current platform contract uses mTLS;
- Telnexa service bearer identity and/or HMAC according to protected contract;
- timestamp validation;
- event ID replay protection;
- method/path/body-bound signature if HMAC is used;
- maximum request size;
- schema version validation;
- server-side tenant/message resolution;
- provider callback payload may never override authoritative tenant ownership.

Normalized envelope:

```json
{
  "schema": "codestra.sms.event.v1",
  "event_id": "uuid-or-provider-derived-id",
  "event_type": "sms.delivered",
  "source": "telnexa",
  "tenant_id": "authoritative-tenant-id",
  "message_id": "telnexa-message-id",
  "correlation_id": "uuid",
  "occurred_at": "2026-08-23T00:00:00Z",
  "data": {}
}
```

Supported core event types:

- `sms.accepted`
- `sms.queued`
- `sms.submitted`
- `sms.delivered`
- `sms.failed`
- `sms.expired`
- `sms.undeliverable`
- `sms.submission_unknown`
- `sms.inbound.received`
- `sms.opted_out`
- `sms.opted_in`
- `sms.help_requested`
- `sms.billing.finalized`
- `sms.billing.released`

## MO / inbound SMS policy

Inbound message flow must remain Telnexa-first:

1. Jasmin receives MO.
2. Telnexa validates provider source and persists the event.
3. Telnexa applies STOP/HELP/START and local compliance changes before Middleware availability is required.
4. Telnexa resolves destination/tenant using its own number/sender/routing records.
5. Telnexa emits normalized MO event to Middleware.
6. Middleware fans out to Odoo/n8n/application through governed interfaces.

Middleware outage must not lose STOP/HELP or leave opt-out dependent on Server A availability.

## Compliance division of responsibility

Middleware may perform business-level eligibility checks, but Telnexa is the final send-time SMS compliance gate before provider dispatch.

Telnexa must enforce:

- approved sender;
- destination normalization;
- tenant send gate;
- consent/opt-out suppression for marketing;
- country policy;
- quiet hours where configured;
- campaign approval where required;
- provider route authorization;
- wallet/credit/quota policy;
- rate/segment accounting.

Middleware cannot override these denials.

## Billing contract

Telnexa is authoritative for SMS segment calculation and provider/sell rate snapshots.

Middleware receives business events, not permission to alter the Telnexa ledger directly.

Required lifecycle:

```text
Telnexa accepts logical message
-> calculate segments/rates
-> reserve amount
-> create durable dispatch job
-> provider definite acceptance
-> finalize reservation exactly once
-> usage record exactly once
```

Definitive pre-submit failure releases the reservation.

Ambiguous provider timeout enters `SUBMISSION_UNKNOWN`; do not release and re-route until reconciliation proves the first submission did not happen.

## Feature flags and release gates

Server A must retain fail-closed production SMS flags until the two-server certification is complete.

Expected effective flags (use canonical discovered names when different):

- `LIVE_SMS_DELIVERY=false`
- `ENABLE_EXTERNAL_DELIVERY=false`
- `EXTERNAL_DELIVERY_ENABLED=false`
- `TELNEXA_PRODUCTION_SMS_ENABLED=false`
- `SMS_PRODUCTION_ENABLED=false`

A software-ready adapter may be deployed with external dispatch disabled.

## Migration away from raw Jasmin

Codex must inventory all Server A references to:

- `sms.telnexa.co/send`
- Jasmin HTTP API credentials
- direct `10.40.0.4:1401` access
- SMPP credentials
- direct DLR relay targets
- provider-specific query/form parameters

Migration process:

1. add Telnexa commercial API client;
2. dual-read/read-only comparison if necessary;
3. run simulator/private sink E2E;
4. switch Middleware send path to Telnexa API while external delivery remains disabled;
5. remove/disable direct Jasmin credential use from Middleware;
6. deny direct Middleware -> Jasmin network path where feasible;
7. prove direct raw send is denied;
8. keep rollback to previous software release, but never reopen a known forbidden direct-provider bypass as a steady-state architecture.

## Network policy

Allowed:

```text
10.40.0.1 Middleware -> 10.40.0.4 Telnexa governed API
10.40.0.4 Telnexa event sender -> 10.40.0.1 Middleware event receiver
```

Denied after migration:

```text
10.40.0.1 -> Jasmin management/jCli
10.40.0.1 -> Jasmin raw HTTP API when a separate private governed Telnexa API exists
10.40.0.1 -> Telnexa billing PostgreSQL
10.40.0.1 -> Redis/RabbitMQ administration on communications host
```

Use exact discovered ports rather than assuming them blindly.

## Timeouts and retry policy

Middleware -> Telnexa API:

- connect timeout bounded;
- request timeout bounded;
- no unbounded retries;
- retry only logical Telnexa API acceptance with the same idempotency key;
- treat 4xx policy denials as terminal;
- 429 honors Retry-After where defined;
- 5xx/network failure follows bounded exponential backoff;
- after Telnexa accepted a message, provider retries are Telnexa's responsibility, not Middleware's.

Telnexa -> Middleware events:

- durable outbox;
- bounded exponential retry;
- replay-safe event ID;
- DLQ after configured limit;
- manual/governed replay endpoint;
- reconciliation can recover a missed downstream event without repeating provider submission or billing.

## Observability

Server A metrics must include at minimum:

- SMS API accepted/denied rate;
- Telnexa API latency/errors;
- integration event inbox depth/age;
- SMS outbox depth/age;
- DLQ count;
- reconciliation drift;
- event replay denials;
- request idempotency conflicts;
- Telnexa availability;
- direct-provider-path drift finding.

Alerts:

- Telnexa unavailable;
- SMS event backlog;
- DLQ > 0 beyond grace window;
- reconciliation drift > 0;
- direct Jasmin path detected;
- live SMS flag unexpectedly enabled before certification.

## Middleware security tests

Required:

- no bearer token -> denied;
- wrong issuer -> denied;
- wrong audience -> denied;
- missing `sms.send` -> denied;
- tenant spoof -> denied;
- cross-tenant message lookup -> denied;
- altered idempotency payload -> conflict;
- unapproved sender -> denied;
- suppressed marketing recipient -> denied;
- provider field injection -> denied/ignored;
- direct Jasmin credential absent from Middleware runtime;
- direct Jasmin network path denied after migration;
- Telnexa event replay -> deduplicated;
- forged Telnexa signature/client identity -> denied.

## Deployment sequence

1. Re-read live repos and runtime.
2. Create a Server A baseline and fresh backup.
3. Implement Middleware source changes in `Codestra-SRL/codestra-middleware`.
4. Implement deployment/config/network changes in `appolon1908-hue/codestra-production-platform` where applicable.
5. Add migrations and tests.
6. Build immutable signed releases through normal CI/review.
7. Run isolated restore of changed durable state.
8. Deploy with SMS delivery still fail-closed.
9. Run Telnexa simulator/private sink E2E.
10. Prove raw Jasmin path no longer exists from Middleware.
11. Proceed to cross-server certification document.

## Server A acceptance matrix

```text
MIDDLEWARE_TELNEXA_CLIENT=PASS
MIDDLEWARE_SMS_API=PASS
MIDDLEWARE_SMS_AUTHZ=PASS
MIDDLEWARE_TENANT_ISOLATION=PASS
MIDDLEWARE_SMS_IDEMPOTENCY=PASS
MIDDLEWARE_TELNEXA_EVENT_RECEIVER=PASS
MIDDLEWARE_EVENT_REPLAY_PROTECTION=PASS
MIDDLEWARE_SMS_OUTBOX=PASS
MIDDLEWARE_SMS_DLQ=PASS
MIDDLEWARE_SMS_RECONCILIATION=PASS
MIDDLEWARE_DIRECT_JASMIN_CREDENTIALS=ABSENT
MIDDLEWARE_TO_JASMIN_DIRECT_PATH=DENIED
TELNEXA_TO_MIDDLEWARE_EVENTS=PASS
EXTERNAL_SMS_DELIVERY=DISABLED
```

Terminal software state for this Server A phase:

`CODESTRA_MIDDLEWARE_TELNEXA_SMS_INTEGRATION_READY`
