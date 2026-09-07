# Telnexa Production SMS Adapter Blueprint

## Purpose

This document is the implementation authority for replacing the current simulator-only commercial submission path and the direct Middleware -> raw Jasmin `/send` dependency with a governed, durable Telnexa production SMS adapter.

The production adapter belongs to Telnexa. Jasmin and carrier SMPP details are transport internals and must not be the commercial or corporate integration surface.

## Current gap

The current Telnexa application already provides strong foundations: tenant isolation, scoped API/OIDC auth, billing wallets/reservations, immutable ledger, rates, sender approval, consent/opt-out controls, provider/route models, durable Message and MessageEvent records, simulator DLR/MO flows, webhook signing, middleware outbox, provider health/circuit state and route preview.

However, the commercial `POST /api/v1/messages` path still executes `send_simulated(...)`, while the production-stack documentation describes Middleware calling Jasmin's raw HTTP `/send` endpoint. This leaves the real carrier submission path outside the commercial control plane and prevents one authoritative transaction from covering authorization, billing reservation, route selection, provider submission, delivery receipts, usage, reconciliation and middleware/customer events.

The target design removes that split.

---

# 1. Target architecture

```text
Customer / Codestra Middleware
          |
          | HTTPS / mTLS + canonical identity
          v
+-----------------------------+
| Telnexa Commercial API      |
| api.telnexa.co              |
+-----------------------------+
          |
          | validate / authorize / consent / sender / billing
          v
+-----------------------------+
| PostgreSQL transaction      |
| Message                     |
| Reservation                 |
| Route request               |
| Dispatch job                |
| Audit                       |
+-----------------------------+
          |
          v
+-----------------------------+
| Dispatch worker             |
| route engine                |
| provider policy             |
| circuit/TPS/backpressure    |
+-----------------------------+
          |
          v
+-----------------------------+
| Jasmin provider adapter     |
| private network only        |
+-----------------------------+
          |
          v
+-----------------------------+
| Jasmin HTTP/SMPP gateway    |
+-----------------------------+
          |
          v
      Carrier SMPP
          |
          v
     Mobile network

DLR / MO
Carrier -> Jasmin -> internal webhook relay -> Telnexa provider-event inbox
        -> state machine / compliance / billing -> integration outbox
        -> Middleware + customer webhooks
```

## Required separation of responsibility

### Telnexa Commercial API
Owns:
- customer and service authentication;
- authoritative tenant resolution;
- API scopes;
- request validation;
- sender authorization;
- consent/suppression checks;
- country/compliance policy;
- billing account state;
- idempotency;
- message creation;
- initial route eligibility;
- billing reservation;
- durable dispatch creation;
- customer-facing message state.

It MUST NOT synchronously depend on a carrier being available before returning a durable accepted response.

### Dispatch worker
Owns:
- claiming durable dispatch jobs;
- exact route selection;
- provider health/circuit evaluation;
- provider TPS and concurrency limits;
- adapter invocation;
- retry classification;
- ambiguous-submission handling;
- provider message ID persistence;
- billing finalization/release decisions;
- durable state/event generation.

### Jasmin adapter
Owns:
- translating a normalized Telnexa submission into Jasmin HTTP API parameters;
- using protected Jasmin credentials;
- creating the internal DLR callback URL without logging or persisting its expanded secret;
- strict timeout and response parsing;
- classifying responses into definitive acceptance, definitive rejection, safe retryable pre-submit failure, and ambiguous outcome;
- returning a normalized result to the dispatch worker.

It MUST NOT own tenant authorization, billing, consent, campaign policy or customer webhooks.

### Internal webhook relay
Owns provider-side ingress authentication and normalization only.

The relay MUST send normalized Jasmin DLR/MO/failure events to the Telnexa internal provider-event API. It MUST NOT bypass Telnexa and directly mutate Odoo or any other business database.

### Provider-event worker
Owns:
- durable event-inbox claiming;
- deduplication;
- provider-message correlation;
- monotonic state transitions;
- MO route resolution;
- STOP/START/HELP compliance processing;
- billing reconciliation events;
- middleware/customer event outbox creation.

### Codestra Middleware
Consumes Telnexa as an application API. Middleware MUST NOT possess Jasmin administrator credentials or call the raw Jasmin HTTP API after migration.

---

# 2. Canonical ingress lanes

## 2.1 Customer SaaS lane

Canonical public API:

`https://api.telnexa.co/api/v1/messages`

Accepted identities:
- tenant-scoped API key;
- canonical OIDC bearer token;
- future scoped customer service account.

The API must use the existing tenant and scope authority. Client-supplied tenant context is never sufficient by itself to establish authorization.

## 2.2 Codestra corporate lane

Codestra Server A -> Telnexa private application API over the private network.

Required controls:
- canonical Keycloak client-credentials identity;
- mTLS where the private service contract requires it;
- fixed service audience;
- `sms.send` scope;
- tenant authorization resolved by the trusted tenant resolver / identity registry;
- correlation ID;
- idempotency key;
- no raw Jasmin credentials.

The same durable send engine is used by customer and corporate calls. There must not be a second hidden send implementation.

## 2.3 Legacy raw Jasmin HTTP path

Current `sms.telnexa.co/send` direct-transport behavior is transitional only.

Target state:
- public commercial clients do not receive Jasmin usernames/passwords;
- Codestra Middleware does not call raw Jasmin `/send`;
- `sms.telnexa.co/send` is either removed from public routing, restricted to a private compatibility lane during migration, or changed to route into the governed Telnexa adapter rather than directly to Jasmin;
- raw Jasmin management/API ports remain unpublished.

Final certification requires `DIRECT_MIDDLEWARE_TO_JASMIN=DENIED`.

---

# 3. Outbound send transaction

## 3.1 API contract

### `POST /api/v1/messages`

Required headers:
- `Authorization: Bearer ...` OR approved `X-API-Key`;
- authoritative tenant context according to the current auth contract;
- `Idempotency-Key`;
- optional `X-Correlation-ID` (generate server-side when absent);
- `Content-Type: application/json`.

Required body fields:
- `billing_account_id`;
- `destination` E.164;
- `sender`;
- `content`;
- `category` (`transactional`, `marketing`, `security`, `system` as supported);
- optional `campaign_id`;
- optional `client_reference`;
- optional DLR preference constrained by policy.

The production API MUST ignore/remove simulator-only outcome controls outside the simulator namespace.

### Successful durable acceptance

HTTP `202 Accepted`

```json
{
  "message_id": "uuid",
  "status": "queued",
  "correlation_id": "uuid",
  "encoding": "GSM-7",
  "segments": 1,
  "estimated_charge": "0.040000",
  "route_state": "eligible",
  "provider_message_id": null
}
```

A 202 means Telnexa has durably recorded the message and billing reservation/dispatch state. It does NOT mean the carrier delivered the SMS.

## 3.2 Transaction boundary

The acceptance transaction must atomically persist:
1. `Message`;
2. billing `Reservation`;
3. immutable rate snapshots;
4. `sms_dispatch_jobs` row;
5. initial `MessageEvent` (`sms.accepted` / `sms.queued`);
6. `Audit` row;
7. optional customer/integration outbox event.

If any component fails, the request must fail without creating a partially billable message.

## 3.3 Existing objects to reuse

REUSE:
- `Message`;
- `MessageEvent`;
- `Reservation` / wallet reservation engine;
- `Usage`;
- `Rate`;
- `Provider`;
- `Route` and route versions where present;
- `Sender`;
- `Contact` and `ConsentRecord`;
- `CountryPolicy`;
- `Audit`;
- `Outbox` / middleware outbox;
- webhook models;
- provider health/circuit state.

EXTEND them rather than adding duplicate business authorities.

---

# 4. Message state machine

Canonical states:

```text
accepted
  -> queued
  -> dispatching
      -> submitted
          -> sent
              -> delivered
              -> expired
              -> undeliverable
              -> failed
      -> retry_wait
      -> submission_unknown
      -> rejected
      -> failed
  -> cancelled (only before provider submission where policy permits)
```

## 4.1 State rules

- `delivered` is terminal and cannot be downgraded by a late `sent`/`submitted` event.
- terminal failure states must not become delivered unless the provider contract explicitly supplies a later authoritative receipt and the state machine has a documented correction transition.
- duplicate provider events do not create duplicate MessageEvents or duplicate billing effects.
- out-of-order DLRs are preserved in the event ledger but cannot violate the monotonic business state.
- every state change records event ID, provider, provider message ID where known, source timestamp, receive timestamp, correlation ID and normalized reason.

## 4.2 Ambiguous submission is first-class

If the HTTP connection times out or fails after Telnexa may have transmitted the request to Jasmin, the adapter MUST return `submission_unknown`.

It MUST NOT blindly fail over to another provider because doing so can create duplicate SMS delivery.

`submission_unknown` requires:
- reconciliation lookup where supported;
- late DLR correlation;
- bounded operator review/recovery;
- a defined expiry policy;
- no automatic second-provider submission without proof that the first provider did not accept.

This is a release-blocking correctness requirement.

---

# 5. Durable dispatch model

Create table `sms_dispatch_jobs`.

Recommended fields:
- `id uuid pk`;
- `tenant_id uuid not null index`;
- `message_id uuid not null unique`;
- `state varchar`;
- `priority int`;
- `available_at timestamptz`;
- `attempt_count int`;
- `max_attempts int`;
- `lease_owner varchar null`;
- `lease_expires_at timestamptz null`;
- `selected_provider_id uuid null`;
- `route_decision_id uuid null`;
- `last_error_class varchar null`;
- `last_error_code varchar null`;
- `last_error_at timestamptz null`;
- `created_at timestamptz`;
- `updated_at timestamptz`.

Indexes:
- `(state, available_at)`;
- `(tenant_id, state)`;
- `(selected_provider_id, state)`;
- `(lease_expires_at)`.

Worker claim must use row locking / `SKIP LOCKED` or an equivalent durable queue pattern. The database remains the source of truth even if RabbitMQ is used for wake-up/fan-out.

Create table `sms_dispatch_attempts`.

Fields:
- `id`;
- `job_id`;
- `message_id`;
- `tenant_id`;
- `attempt_number`;
- `provider_id`;
- `adapter_type`;
- `route_version`;
- `request_fingerprint`;
- `started_at`;
- `completed_at`;
- `outcome` (`ACCEPTED`, `DEFINITIVE_REJECT`, `SAFE_RETRY`, `AMBIGUOUS`, `INTERNAL_ERROR`);
- `provider_message_id`;
- `http_status` where applicable;
- `provider_code`;
- `error_class`;
- `latency_ms`;
- `secret_free_evidence_json`.

Attempt rows are immutable except completion fields.

---

# 6. Provider adapter interface

Implement an internal interface such as:

```python
class SmsProviderAdapter(Protocol):
    async def submit(self, submission: NormalizedSubmission) -> SubmissionResult: ...
    async def health(self) -> ProviderHealthResult: ...
    def normalize_dlr(self, event: ProviderEvent) -> NormalizedDeliveryReceipt: ...
    def normalize_mo(self, event: ProviderEvent) -> NormalizedInboundMessage: ...
```

`NormalizedSubmission` contains only normalized business/transport fields:
- Telnexa message ID;
- tenant ID;
- correlation ID;
- E.164 destination;
- validated sender;
- content;
- encoding;
- segment count;
- DLR level;
- provider route metadata;
- callback reference;
- optional validity period.

It MUST NOT contain billing wallet objects or tenant auth credentials.

`SubmissionResult`:
- `outcome`;
- `provider_message_id`;
- `provider_code`;
- `retryable`;
- `ambiguous`;
- `accepted_at`;
- `safe_metadata`.

---

# 7. Jasmin HTTP adapter

Implement `JasminHttpAdapter` as the first production adapter.

## 7.1 Network

The adapter connects to Jasmin only over the private Docker/provider network. Raw Jasmin HTTP API ports remain unpublished.

## 7.2 Credentials

Credentials are loaded from root-owned secret files / approved secret manager references:
- HTTP username reference;
- HTTP password reference;
- active DLR source key ID;
- DLR source token reference.

Never:
- store expanded credentials in PostgreSQL;
- return them from admin APIs;
- put them in logs;
- put them in dispatch evidence;
- commit them to Git.

Database/provider config stores only secret references and non-secret configuration.

## 7.3 Submission mapping

Map normalized fields to Jasmin HTTP API:
- `username`;
- `password`;
- `to`;
- `from`;
- `content`;
- `coding` (`0` GSM-7 where valid, `8` UCS-2);
- `dlr=yes`;
- approved `dlr-level`;
- `dlr-url` internal relay URL;
- `dlr-method=POST` where supported/configured.

The internal DLR URL must be constructed from protected source credentials at runtime. Never persist the expanded URL.

## 7.4 HTTP behavior

Require:
- short connection timeout;
- bounded total timeout;
- redirects disabled;
- environment proxy disabled unless explicitly governed;
- response size limit;
- exact response parser;
- secret-redacted exceptions.

Classify Jasmin responses into:
- accepted with message ID;
- definitive authentication failure;
- definitive route rejection;
- policy/format rejection;
- safe retryable pre-submit transport failure;
- ambiguous submission.

## 7.5 Idempotency

Telnexa idempotency is authoritative. Do not assume Jasmin's HTTP API supplies an application idempotency primitive.

A dispatch job can be safely retried only when the previous attempt is proven not accepted. Ambiguous submissions enter reconciliation, not automatic failover.

---

# 8. Provider and credential registry

EXTEND existing `Provider` with or normalize through a provider configuration table.

Recommended non-secret configuration:
- `adapter_type` (`jasmin_http`, future `direct_smpp`, `rest_provider`);
- `state`;
- `environment`;
- `base_url` or private service name;
- `credential_reference`;
- `dlr_source_key_id`;
- `tps_limit`;
- `max_inflight`;
- `connect_timeout_ms`;
- `request_timeout_ms`;
- `supports_query`;
- `supports_mo`;
- `supports_dlr`;
- `supports_unicode`;
- `max_message_bytes`;
- `health_score`;
- `circuit_state`;
- `last_success_at`;
- `last_failure_at`;
- `failure_streak`;
- `created_at` / `updated_at`.

Create versioned `provider_connector_configs` if changing a provider config must be auditable/reproducible.

Provider secrets are provisioned by the restricted Telnexa operator. Admin UI/API may reference a secret version but must never accept arbitrary plaintext credentials into durable logs or API responses.

---

# 9. Route engine

Route selection must be deterministic and evidence-producing.

Inputs:
- tenant;
- destination prefix/country;
- optional network/MCC/MNC if available;
- sender type;
- campaign/category;
- country policy;
- provider capabilities;
- provider enabled state;
- provider circuit/health;
- per-provider TPS/inflight;
- tenant route allow/deny policy;
- effective-dated provider cost;
- sell price/minimum margin;
- route priority/weight;
- route version.

Create `sms_route_decisions`:
- ID;
- message ID;
- tenant ID;
- destination prefix/country;
- selected provider;
- selected route/version;
- candidate summary without secrets;
- rejection reasons for excluded candidates;
- effective provider/sell snapshots;
- decision hash;
- created timestamp.

The decision used for a submitted SMS is immutable.

No route -> definitive `rejected/no_route` before carrier submission and release billing reservation.

## 9.1 Failover rules

Failover allowed only after a definitive safe-to-retry result.

No automatic failover after:
- provider acceptance;
- provider message ID received;
- ambiguous timeout;
- connection loss after submission might have reached Jasmin.

Circuit breaker transitions must be bounded and observable.

---

# 10. Billing coupling

Use existing wallet/reservation/ledger engine.

## Acceptance

Before a dispatch job becomes eligible:
- calculate encoding/segments;
- calculate effective sell and provider rate snapshots;
- validate prepaid/postpaid rules;
- reserve sell amount atomically.

## Definitive pre-submit failure

Release reservation idempotently.

## Provider accepted

Finalize customer charge exactly once according to current Telnexa commercial policy. Create/update Usage exactly once.

## Cost updates

Provider-cost changes or final provider billing reconciliation must use immutable adjustments rather than rewriting old ledger entries.

## DLR

DLR changes delivery status but must not double-finalize billing.

Final requirements:
- `DUPLICATE_BILLING_EFFECTS=0`;
- `MESSAGE_USAGE_CARDINALITY=1` per billable message;
- replayed DLR/MO has zero duplicate financial effect.

---

# 11. Provider event ingress

The webhook relay remains the source-authentication edge for Jasmin callbacks.

Change target architecture from relay -> Middleware direct to:

```text
Jasmin
  -> webhook-relay
  -> Telnexa internal provider-event endpoint
  -> durable provider event inbox
  -> Telnexa event worker
  -> Middleware/customer outbox
```

## Internal endpoint

`POST /internal/v1/provider-events/jasmin`

Not publicly routed.

Authentication:
- private Docker/network access;
- relay identity/HMAC or fixed source credential;
- method/path/body-bound signature;
- timestamp/replay window;
- source key ID;
- event ID.

The endpoint must persist before returning success.

Create `sms_provider_event_inbox`:
- `id`;
- `source`;
- `source_key_id`;
- `event_id`;
- `event_type` (`DLR`, `MO`, `FAILURE`);
- `provider_message_id`;
- `message_id` nullable until resolved;
- `tenant_id` nullable until resolved;
- `payload_hash`;
- encrypted/raw payload reference or redacted normalized payload according to retention policy;
- `occurred_at`;
- `received_at`;
- `state`;
- `attempts`;
- `last_error`;
- unique `(source, event_id)`.

Do not trust tenant ID supplied by the provider callback. Resolve tenant/message from authoritative mappings.

---

# 12. DLR processing

Normalize provider states to:
- `submitted`;
- `sent`;
- `delivered`;
- `failed`;
- `expired`;
- `undeliverable`;
- `unknown`.

Persist every unique receipt in `sms_provider_receipts` or existing MessageEvent with enough source evidence to audit ordering.

Correlation order:
1. provider message ID mapping;
2. Telnexa message/correlation reference if the adapter encoded one;
3. documented reconciliation fallback;
4. otherwise quarantine as `UNMATCHED_PROVIDER_EVENT`.

Never assign a callback to a tenant from a caller-provided tenant header alone.

After processing, enqueue signed integration events such as:
- `sms.submitted`;
- `sms.sent`;
- `sms.delivered`;
- `sms.failed`;
- `sms.expired`;
- `sms.undeliverable`.

---

# 13. MO processing

Normalize MO fields:
- provider event ID;
- provider message ID;
- source MSISDN;
- destination number/shortcode;
- content;
- encoding;
- provider;
- received timestamp;
- carrier metadata allowlist.

Tenant resolution uses the assigned Telnexa inbound number/route, not provider-supplied tenant data.

Persist to existing `InboundMessage`/conversation model.

Compliance keywords:
- STOP family -> opt out and suppression;
- START/UNSTOP family -> opt-in workflow only where legally/policy allowed;
- HELP -> help-request event;
- unknown text -> normal MO event.

STOP processing must complete locally even if Middleware is unavailable. Middleware notification retries asynchronously.

Integration events:
- `sms.inbound.received`;
- `sms.opted_out`;
- `sms.opted_in` where allowed;
- `sms.help_requested`.

---

# 14. Middleware integration contract

Telnexa -> Middleware events use durable outbox and the existing canonical HMAC/service-auth pattern.

Required event envelope:

```json
{
  "event_id": "uuid",
  "event_type": "sms.delivered",
  "source": "telnexa",
  "tenant_id": "authoritative-tenant",
  "message_id": "telnexa-message-id",
  "correlation_id": "uuid",
  "occurred_at": "RFC3339",
  "schema_version": 1,
  "payload": {}
}
```

Required headers:
- bearer service identity where configured;
- `X-Signature-Version`;
- `X-Telnexa-Timestamp`;
- `X-Telnexa-Event-Id`;
- `X-Telnexa-Signature`;
- correlation ID.

Middleware receiver must enforce:
- source identity;
- signature;
- timestamp;
- replay prevention;
- schema version;
- tenant authorization;
- idempotency.

If Middleware is unavailable, Telnexa retains the event and retries with bounded backoff/DLQ. SMS sending and compliance state remain authoritative in Telnexa.

---

# 15. Customer webhooks

Reuse existing Webhook/WebhookDelivery models.

Events must be created from the same durable message/event state as Middleware events.

Rules:
- HMAC signatures;
- timestamp;
- event ID;
- replay-safe customer contract;
- URL SSRF validation;
- redirect denial;
- bounded timeout;
- retry/backoff;
- DLQ;
- manual resend with audit;
- no secret/body logging.

Customer webhook failure must not cause the carrier submission to retry.

---

# 16. Production send gates and canary

Production sending remains fail-closed by default.

Required layered controls:
- environment-level `TELNEXA_PRODUCTION_SMS_ENABLED=false` default;
- provider routing enabled flag false by default;
- DB tenant send gate;
- sender approval;
- route approval;
- provider enabled state;
- country policy;
- compliance/consent;
- billing eligibility.

Add durable canary gate `sms_production_canary_gates` with:
- gate key;
- allowed tenant;
- allowed sender;
- allowed destination(s);
- maximum deliveries/submissions;
- reserved count;
- claimed count;
- expires_at;
- approved_by/reference;
- enabled;
- timestamps.

A real canary must be server-side limited. A client cannot change the destination and inherit the authorization.

Recommended progression:

```text
SIMULATOR_ONLY
-> PROVIDER_SANDBOX
-> SINGLE_DESTINATION_CANARY
-> TENANT_CANARY
-> BOUNDED_PRODUCTION
-> PRODUCTION
```

Each transition requires evidence and rollback.

---

# 17. APIs

## Existing commercial APIs to retain/extend

- `POST /api/v1/messages`
- `POST /api/v1/messages/bulk`
- `GET /api/v1/messages`
- `GET /api/v1/messages/{message_id}`
- `GET /api/v1/messages/{message_id}/events`
- contacts/consent/opt-out APIs;
- senders;
- templates/campaigns;
- rates;
- webhooks;
- billing/usage;
- admin route preview.

## New/extended operations APIs

Protected platform/admin scope only:

- `GET /api/v1/admin/providers`
- `GET /api/v1/admin/providers/{provider_id}`
- `GET /api/v1/admin/providers/{provider_id}/health`
- `POST /api/v1/admin/providers/{provider_id}/probe` (NO SMS)
- `POST /api/v1/admin/providers/{provider_id}/circuit/open`
- `POST /api/v1/admin/providers/{provider_id}/circuit/close`
- `GET /api/v1/admin/routes/preview`
- `GET /api/v1/admin/dispatch/jobs`
- `GET /api/v1/admin/dispatch/jobs/{job_id}`
- `POST /api/v1/admin/dispatch/jobs/{job_id}/retry`
- `POST /api/v1/admin/dispatch/jobs/{job_id}/reconcile`
- `GET /api/v1/admin/provider-events`
- `GET /api/v1/admin/reconciliation`
- `POST /api/v1/admin/reconciliation`
- `GET /api/v1/admin/send-gates`
- `POST /api/v1/admin/send-gates/{gate_id}/close`

No admin endpoint returns provider credential values.

## Internal APIs

Not exposed publicly:
- `POST /internal/v1/provider-events/jasmin`;
- `GET /internal/v1/adapter/health`;
- optional provider-query/reconciliation endpoints called only by restricted workers.

---

# 18. Database migration plan

Prefer additive forward migrations.

## New tables

1. `sms_dispatch_jobs`
2. `sms_dispatch_attempts`
3. `sms_route_decisions`
4. `sms_provider_event_inbox`
5. `sms_provider_event_attempts`
6. `sms_provider_receipts` if MessageEvent is not sufficient
7. `sms_reconciliation_cases`
8. `sms_production_canary_gates`
9. `provider_connector_configs` if versioned provider config is implemented separately

## Existing table extensions

`messages`:
- optional `route_decision_id`;
- optional `dispatch_job_id`;
- optional `submitted_at`;
- optional `delivered_at`;
- optional `terminal_at`;
- optional `failure_code`;
- optional `submission_certainty`.

`providers`:
- adapter/config/capability/timeout/TPS metadata as needed.

## Constraints

- message/dispatch one-to-one;
- unique provider-event source/event ID;
- unique `(provider_id, provider_message_id)` where provider guarantees uniqueness;
- unique route decision per submitted attempt where appropriate;
- attempt number unique per job;
- no negative attempt counts;
- tenant IDs indexed everywhere tenant-owned;
- existing RLS model extended to all new tenant-owned tables.

## RLS

All tenant-owned adapter tables require RLS or the repository's equivalent tenant-isolation migration pattern.

Platform workers use dedicated roles with only the minimum permissions needed.

---

# 19. Worker processes

Add services:
- `sms-dispatch-worker`;
- `sms-provider-event-worker`;
- `sms-reconciliation-worker`.

Optional later:
- `sms-webhook-worker` if customer delivery is split from the existing billing worker.

Workers must:
- run non-root;
- have read-only filesystem where practical;
- drop capabilities;
- use health/heartbeat;
- not publish host ports;
- have only required networks;
- receive secrets through secret files;
- use bounded DB pools;
- stop gracefully;
- release/expire leases safely.

RabbitMQ can be a wake-up/transport layer, but durable job state and idempotency remain persisted.

---

# 20. Concurrency and throughput

Enforce at least:
- tenant API rate limit;
- tenant send TPS;
- provider TPS;
- provider max in-flight;
- route/category quotas;
- bulk endpoint bounded request size;
- queue backpressure;
- per-worker concurrency limit.

Do not rely on in-process counters for multi-worker production enforcement. Use Redis/database/shared coordination according to the existing architecture.

Provider token-bucket keys must include provider/environment and must expire/recover safely.

---

# 21. Security requirements

## Authentication
- canonical issuer `https://auth.codestra.co/realms/codestra` for Codestra identity where applicable;
- exact audience/authorized party validation;
- scoped API keys hashed with the existing secure model;
- internal source identity for webhook relay;
- no browser-controlled privileged identity headers.

## Network
Public expected:
- 80/443 only for web/API ingress as currently designed.

Must remain private/unpublished:
- PostgreSQL;
- Redis;
- RabbitMQ;
- Jasmin HTTP API;
- Jasmin management console;
- Jasmin SMPP server unless a separately governed customer SMPP listener is intentionally exposed via VPN/IP allowlist;
- Prometheus/exporters;
- Docker API.

## Secrets
Never store/log:
- Jasmin password;
- carrier SMPP credentials;
- provider DLR token;
- middleware HMAC secret;
- customer webhook secrets;
- Keycloak client secret.

Use secret references / `*_FILE` and root-owned operator-controlled installation.

## SSRF
Any configurable customer/provider callback URL must enforce the existing SSRF protections, redirects off, and destination policy.

---

# 22. Compliance and policy

Before queuing a production SMS enforce:
- tenant active;
- billing account active/not frozen;
- approved sender;
- destination allowed by country policy;
- sender type allowed in country;
- marketing consent when category=marketing;
- global/tenant/campaign suppression;
- quiet hours where configured;
- campaign approval where applicable;
- A2P/registration metadata where required;
- message length/segment policy;
- content/purpose policy where configured;
- send gate.

STOP/HELP must remain locally enforceable during Middleware outage.

---

# 23. Observability

Add Prometheus metrics:
- `telnexa_sms_dispatch_jobs{state}`;
- `telnexa_sms_dispatch_oldest_seconds`;
- `telnexa_sms_dispatch_attempts_total{provider,outcome}`;
- `telnexa_sms_provider_submit_latency_seconds{provider}`;
- `telnexa_sms_provider_circuit_state{provider}`;
- `telnexa_sms_provider_health_score{provider}`;
- `telnexa_sms_provider_inflight{provider}`;
- `telnexa_sms_provider_throttle_total{provider}`;
- `telnexa_sms_submission_unknown_total{provider}`;
- `telnexa_sms_dlr_events_total{status}`;
- `telnexa_sms_dlr_lag_seconds{provider}`;
- `telnexa_sms_mo_events_total{type}`;
- `telnexa_sms_unmatched_provider_events`;
- `telnexa_sms_middleware_outbox{state}`;
- `telnexa_sms_customer_webhooks{state}`;
- `telnexa_sms_billing_reconciliation_drift`;
- `telnexa_sms_canary_remaining`.

Alerts:
- dispatch queue age;
- provider circuit open;
- provider error rate;
- submission_unknown spike;
- unmatched DLR/MO;
- middleware DLQ;
- customer webhook backlog;
- billing drift;
- no active provider route;
- worker heartbeat missing;
- canary unexpected consumption.

Logs must include IDs/codes, never message bodies by default and never secrets.

---

# 24. Reconciliation

Create `sms_reconciliation_cases` for:
- ambiguous submission;
- provider ID missing;
- unmatched DLR;
- duplicate provider ID collision;
- billing status drift;
- usage missing;
- reservation stuck;
- message terminal but dispatch job active;
- provider accepted but customer state not submitted.

Admin reconciliation can fix internal state only through explicit, audited, idempotent actions.

It must not re-send an SMS merely to make data consistent.

---

# 25. Testing

## Unit
- state transitions;
- GSM-7/UCS-2 mapping;
- Jasmin request generation;
- Jasmin response parser;
- routing;
- safe failover;
- ambiguous result;
- DLR normalization;
- MO normalization;
- consent/sender/country policy;
- billing reserve/finalize/release;
- canary gate.

## Integration
Use a deterministic fake Jasmin HTTP service.

Test:
- accepted submission;
- auth failure;
- no route;
- HTTP 5xx;
- connection refused before write;
- response timeout/ambiguous;
- malformed response;
- Unicode/multipart;
- provider throttling;
- duplicate send request;
- altered idempotency payload;
- duplicate DLR;
- out-of-order DLR;
- MO STOP/HELP;
- middleware outage;
- customer webhook outage;
- DB restart;
- worker restart.

## Security
- cross-tenant message read/write denied;
- tenant spoof denied;
- wrong issuer/audience/azp denied;
- missing/incorrect scope denied;
- provider event replay denied;
- signature method/path/body tamper denied;
- SSRF denied;
- direct Jasmin public access denied;
- provider credentials absent from logs/evidence;
- RLS pass;
- raw DB/broker ports unpublished.

## Load
Synthetic only until live authorization.

Measure:
- API accept RPS;
- dispatch worker throughput;
- provider TPS enforcement;
- queue age;
- P50/P95/P99 accept latency;
- DB connections;
- Redis/RabbitMQ pressure;
- worker crash recovery;
- no duplicate business effect.

---

# 26. Deployment and migration

## Stage A - source
1. implement additive migration;
2. implement adapter interface + Jasmin adapter;
3. change `POST /api/v1/messages` from `send_simulated` to durable acceptance when production mode is selected;
4. preserve simulator under explicit simulator/test namespace;
5. add dispatch/provider-event/reconciliation workers;
6. retarget internal webhook relay to Telnexa provider-event ingress;
7. add metrics/alerts;
8. update docs/OpenAPI/tests.

## Stage B - isolated
- restore production-like billing DB into isolated network;
- run migrations;
- fake-Jasmin E2E;
- rollback/reapply migration;
- load tests;
- secret/dependency/container scans.

## Stage C - provider sandbox
- approved sandbox credentials only;
- explicit authorized destination(s);
- MT -> DLR -> MO tests;
- Unicode/multipart;
- throttling;
- no marketing/bulk.

## Stage D - single real canary
- exact tenant;
- exact sender;
- exact destination;
- max 1 submission/delivery;
- durable canary reservation;
- verify billing=1 logical message;
- DLR correlation;
- middleware event;
- customer webhook if configured;
- close gate immediately.

## Stage E - bounded production
Only after explicit approval.

---

# 27. Backward compatibility

Existing customer APIs remain compatible where safe.

`simulator_outcome` must not influence production mode and should be rejected or ignored outside simulator routes.

Current message IDs remain stable.

Existing historical Message/Usage/ledger records are not rewritten.

The migration must tolerate current production rows.

Legacy direct-Jasmin integrations receive a documented migration path to `/api/v1/messages` before public raw transport is disabled.

---

# 28. Code layout recommendation

Use the current Python billing/control-plane package unless implementation evidence justifies another package.

Recommended additions:

```text
billing/
  adapters/
    __init__.py
    base.py
    jasmin_http.py
    errors.py
  dispatch.py
  dispatch_worker.py
  provider_events.py
  provider_event_worker.py
  routing.py
  reconciliation.py
  reconciliation_worker.py
  state_machine.py
  production_gates.py
```

Do not put all new logic into `billing/app.py`.

Keep API handlers thin and transactional domain functions testable.

---

# 29. Definition of done

Required final evidence:

```text
COMMERCIAL_API_DURABLE_ACCEPTANCE=PASS
SIMULATOR_SEPARATED_FROM_PRODUCTION=PASS
JASMIN_ADAPTER=PASS
JASMIN_CREDENTIAL_ISOLATION=PASS
ROUTE_ENGINE=PASS
PROVIDER_CIRCUIT_BREAKER=PASS
PROVIDER_TPS_ENFORCEMENT=PASS
SAFE_FAILOVER=PASS
AMBIGUOUS_SUBMISSION_HANDLING=PASS
DISPATCH_QUEUE=PASS
DISPATCH_WORKER_RECOVERY=PASS
DLR_INGEST=PASS
DLR_IDEMPOTENCY=PASS
DLR_OUT_OF_ORDER=PASS
MO_INGEST=PASS
STOP_HELP_COMPLIANCE=PASS
BILLING_RESERVATION=PASS
BILLING_FINALIZATION=PASS
BILLING_RELEASE=PASS
DUPLICATE_BILLING_EFFECTS=0
MIDDLEWARE_EVENT_OUTBOX=PASS
MIDDLEWARE_RETRY_DLQ=PASS
CUSTOMER_WEBHOOKS=PASS
TENANT_ISOLATION=PASS
RLS=PASS
PROVIDER_EVENT_REPLAY=DENIED
DIRECT_MIDDLEWARE_TO_JASMIN=DENIED
RAW_JASMIN_PUBLIC=DENIED
POSTGRESQL_PUBLIC=DENIED
REDIS_PUBLIC=DENIED
RABBITMQ_PUBLIC=DENIED
PRODUCTION_CANARY_GATE=PASS
SINGLE_REAL_CANARY=PASS_OR_BLOCKED_EXTERNAL
SECRET_SCAN=PASS
DEPENDENCY_SCAN=PASS
CONTAINER_SCAN=PASS
BACKUP=PASS
ISOLATED_RESTORE=PASS
ROLLBACK_READY=PASS
```

Terminal software status before external provider authorization may be:

`TELNEXA_PRODUCTION_SMS_ADAPTER_SOFTWARE_READY_PROVIDER_GATED`

Terminal production status after approved carrier and live canary evidence:

`TELNEXA_PRODUCTION_SMS_ADAPTER_CERTIFIED`

---

# 30. Non-negotiable rules

- Do not bypass billing by calling Jasmin directly.
- Do not bypass Telnexa tenant authorization.
- Do not let provider callbacks choose the tenant.
- Do not retry ambiguous submissions onto another provider automatically.
- Do not double-bill retries or DLR replays.
- Do not put provider credentials in PostgreSQL/Git/logs.
- Do not expose Jasmin admin/HTTP/SMPP management ports publicly.
- Do not let Middleware write Telnexa DB directly.
- Do not let n8n write Telnexa DB directly.
- Do not use customer webhooks as part of carrier submission success criteria.
- Do not send live SMS during development/certification except an explicitly authorized bounded canary.
- Do not patch production-only runtime without an authoritative source change and governed release.
