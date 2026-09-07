# CODEX MISSION — TELNEXA PRODUCTION SMS ADAPTER

## Mission

Implement, test, release and certify the Telnexa production SMS adapter described by:

1. `docs/TELNEXA_PRODUCTION_SMS_ADAPTER_BLUEPRINT.md`
2. `config/telnexa-production-sms-adapter-contract.yaml`

Do not return another design-only document. The planning design already exists. Convert it into governed source, migrations, tests, workers, runtime configuration, monitoring, deployment tooling and certification evidence.

## Repository

`appolon1908-hue/telnexa`

Planning branch:

`planning/production-sms-adapter-v1`

The observed base at planning creation was:

`79f2a2ff84aad6a5857d2ef15fcd472f1575dae4`

Do not assume that SHA remains current. Re-read `main`, all open PRs and current production release state before implementation.

## Infrastructure

Telnexa/Klyrow communications host:
- public: `37.27.128.39`
- private: `10.40.0.4`

Codestra Server A / Middleware:
- public: `65.109.65.169`
- private: `10.40.0.1`

Voice/VICIdial:
- private: `10.40.0.2`

Canonical identity issuer:
- `https://auth.codestra.co/realms/codestra`

Do not treat `10.40.0.2` as a Telnexa provider/application endpoint. That address is the voice host.

## Core defect to remediate

The existing commercial `POST /api/v1/messages` implementation calls `send_simulated(...)`. The existing production-stack documentation also describes Middleware calling Jasmin's raw `/send` transport API.

This creates two incomplete paths:

1. the commercial control plane has strong billing/tenant/compliance state but does not actually dispatch to the carrier path;
2. the raw Jasmin path can bypass the commercial durable transaction if used directly.

The target is one authoritative path:

```text
Client / Middleware
-> Telnexa API
-> authorization / policy / billing reservation
-> Message + dispatch job
-> dispatch worker
-> route engine
-> Jasmin adapter
-> Jasmin
-> carrier
-> DLR/MO
-> Telnexa durable provider event inbox
-> state/billing/compliance
-> Middleware/customer webhooks
```

## Non-negotiable architecture

- Telnexa owns the provider adapter.
- Jasmin is transport only.
- Middleware must not own/store Jasmin credentials after migration.
- `POST /api/v1/messages` remains the authoritative commercial send API.
- durable acceptance occurs before provider dispatch.
- billing reservation is part of acceptance.
- provider callbacks update Telnexa before downstream business events are emitted.
- provider callbacks never choose the tenant.
- ambiguous submissions are reconciled and never blindly failed over.
- duplicate requests/events must not duplicate SMS or billing effects.
- direct Odoo/n8n DB access is forbidden.
- external production sending remains fail-closed until an approved canary.

## Phase 0 — Live reconciliation

Before source changes:

1. fetch `main`;
2. inventory open Telnexa PRs;
3. inspect current `billing/app.py`, `billing/product_api.py`, `billing/engine.py`, `billing/models.py`, migrations, webhook relay, Jasmin config and Compose;
4. identify current production image/release if accessible;
5. identify current live service topology read-only;
6. identify authoritative provider/route models and migrations;
7. identify existing billing reservation/finalization/release functions;
8. identify existing MessageEvent, MO/DLR, webhook and middleware outbox implementations;
9. identify branch protection and CI requirements.

Create a gap matrix:

```text
CAPABILITY | REUSE | EXTEND | NEW | DEPRECATE | BLOCKED_EXTERNAL
```

Do not create duplicate billing, tenant, route, provider, message or event authorities.

## Phase 1 — Source branch

Create a new implementation branch from the current protected `main`, not from a stale planning SHA.

Recommended name:

`agent/production-sms-adapter-v1`

Do not modify `main` directly.

## Phase 2 — Database migration

Add a forward migration for the adapter.

Required new durable structures unless an existing table cleanly fulfills the exact contract:

- `sms_dispatch_jobs`
- `sms_dispatch_attempts`
- `sms_route_decisions`
- `sms_provider_event_inbox`
- `sms_provider_event_attempts`
- `sms_provider_receipts` where MessageEvent is insufficient
- `sms_reconciliation_cases`
- `sms_production_canary_gates`
- `provider_connector_configs` if versioning provider config separately is cleaner than extending Provider

Extend `messages` and `providers` only as required by the blueprint.

Migration requirements:
- additive/forward-safe;
- current rows remain valid;
- indexes created;
- unique constraints enforce idempotency;
- all new tenant-owned tables get RLS using the existing Telnexa pattern;
- migration reapply is safe according to repository standards;
- downgrade/rollback procedure documented even if schema rollback is intentionally manual.

Test on disposable PostgreSQL:

```text
BASE -> NEW -> VALIDATE -> REAPPLY -> VALIDATE
```

Then restore a production-like backup into isolation and apply the migration there.

## Phase 3 — Refactor API/domain boundaries

Do not continue putting all logic into `billing/app.py`.

Add a clean package structure similar to:

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

Reuse existing engine/billing functions instead of duplicating monetary logic.

API handlers should validate/authenticate then call transactional domain services.

## Phase 4 — Production-safe commercial send acceptance

Replace production use of `send_simulated(...)` behind `POST /api/v1/messages`.

Keep simulator behavior in explicit test/simulator mode only.

The production acceptance transaction must:

1. authenticate service/customer;
2. resolve authoritative tenant;
3. enforce scope;
4. validate billing account belongs to tenant;
5. enforce idempotency key and canonical request hash;
6. enforce marketing consent/suppression where applicable;
7. enforce sender approval;
8. enforce country/sender/quiet-hour/campaign policy;
9. calculate GSM-7/UCS-2 and segments;
10. freeze effective provider/sell rate snapshots;
11. reserve billing amount atomically;
12. create Message;
13. create initial MessageEvent;
14. create durable dispatch job;
15. create Audit;
16. commit once;
17. return 202 queued.

If one item fails, no partial billable/dispatchable message remains.

Remove/deny simulator outcome controls in production requests.

Required tests:
- changed idempotency payload -> 409;
- missing sender -> denied;
- unapproved sender -> denied;
- marketing suppression -> denied;
- tenant mismatch -> denied;
- insufficient balance -> no dispatch job;
- successful request -> exactly one Message, Reservation and DispatchJob.

## Phase 5 — Message state machine

Implement the canonical state machine from the blueprint.

Mandatory states:

`accepted, queued, dispatching, submitted, sent, delivered, retry_wait, submission_unknown, rejected, failed, expired, undeliverable, cancelled`

Rules:
- delivered cannot downgrade;
- duplicate DLR no duplicate state/billing;
- out-of-order DLR event is recorded but business state stays valid;
- terminal transitions are explicit;
- every transition emits exactly one durable MessageEvent per unique source event.

Write table-driven tests for every permitted and rejected transition.

## Phase 6 — Provider adapter interface

Implement a provider-neutral interface.

The dispatch worker must not contain Jasmin-specific parameter construction.

Implement normalized submission/result models and adapter errors.

Submission result classes:
- ACCEPTED
- DEFINITIVE_REJECT
- SAFE_RETRY
- AMBIGUOUS
- INTERNAL_ERROR

Test adapters via dependency injection/fakes.

## Phase 7 — Jasmin HTTP adapter

Implement the first production adapter.

Requirements:
- private network endpoint only;
- protected secret-file credentials;
- no credential values in DB/log/evidence;
- exact mapping for to/from/content/coding/DLR;
- runtime-only expanded DLR URL;
- redirects disabled;
- trust_env disabled unless explicitly governed;
- bounded connect/read/total timeouts;
- response-size cap;
- strict Jasmin response parser;
- secret-redacted errors;
- deterministic adapter health probe that sends NO SMS.

Test using a fake Jasmin HTTP server.

Test cases:
- accepted response with provider message ID;
- auth reject;
- no route;
- malformed destination/content reject;
- 5xx before acceptance;
- connection refused;
- timeout with ambiguous acceptance possibility;
- malformed response;
- Unicode coding=8;
- multipart message;
- DLR URL construction without logging token.

## Phase 8 — Dispatch worker

Implement `sms-dispatch-worker`.

Claim durable jobs using `SKIP LOCKED` or equivalent durable lease semantics.

Worker lifecycle:

```text
claim
-> load message/rates/policy
-> route
-> acquire shared provider TPS/inflight allowance
-> record route decision
-> create attempt
-> call adapter
-> classify result
-> update message/job/billing/events
-> commit
```

Use Redis/database shared limits, not in-process-only counters.

Worker must have:
- heartbeat;
- lease expiry/recovery;
- graceful shutdown;
- max concurrency;
- backpressure;
- bounded retry;
- dead-letter/reconciliation path.

## Phase 9 — Route engine

Extend/reuse Provider and Route.

Inputs must include:
- destination country/prefix;
- network if known;
- sender type;
- category;
- country policy;
- provider capability;
- provider state;
- circuit state;
- health score;
- TPS/inflight;
- tenant allow/deny route policy;
- provider rate;
- sell rate/minimum margin;
- route priority/weight/version.

Persist immutable `sms_route_decisions`.

Test:
- exact prefix precedence;
- disabled route excluded;
- open circuit excluded;
- provider capability mismatch excluded;
- negative margin denied unless explicit governed exception;
- deterministic route evidence;
- no route releases reservation.

## Phase 10 — Safe failover and ambiguous submission

Implement the blueprint exactly.

Automatic failover only after proof that the previous provider did not accept.

Do NOT fail over when:
- provider message ID exists;
- accepted response was returned;
- timeout occurred after request may have reached Jasmin;
- connection loss is ambiguous.

Ambiguous -> `submission_unknown` + reconciliation case.

Create tests proving an ambiguous primary attempt never invokes the backup adapter.

This is a P0 release gate.

## Phase 11 — Billing integration

Use existing wallet/reservation/immutable ledger system.

Required behavior:
- reserve before queue;
- release on definitive pre-submit failure;
- finalize exactly once on provider acceptance according to current commercial policy;
- create Usage exactly once;
- DLR replays cannot bill again;
- provider-cost adjustments use immutable ledger/reconciliation adjustments;
- stuck reservations detected by reconciliation.

Tests:

```text
DUPLICATE_BILLING_EFFECTS=0
USAGE_PER_BILLABLE_MESSAGE=1
REPLAYED_DLR_BILLING_DELTA=0
```

## Phase 12 — Provider event ingress

Change callback architecture to:

`Jasmin -> webhook-relay -> Telnexa provider-event inbox -> Telnexa processing -> Middleware/customer events`

Do not leave the relay as a direct business-state bypass to Middleware.

Add private endpoint:

`POST /internal/v1/provider-events/jasmin`

Endpoint must:
- authenticate source;
- verify method/path/body-bound signature;
- verify timestamp;
- reject replay;
- enforce body size;
- persist unique event before acknowledging;
- not trust callback tenant ID.

Retarget relay configuration and tests to this endpoint.

## Phase 13 — DLR processor

Normalize provider DLR states.

Correlate by authoritative provider message ID/Telnexa mapping.

Unmatched DLR -> reconciliation case, never guessed tenant.

Test:
- duplicate DLR;
- out-of-order DLR;
- delivered then sent remains delivered;
- failed terminal path;
- unmatched provider ID;
- provider event replay;
- DLR creates one signed Middleware/customer event.

## Phase 14 — MO processor

Resolve tenant from Telnexa inbound-number/route assignment.

Persist InboundMessage before downstream event.

STOP/HELP/START processing stays local and durable.

Middleware outage must not stop opt-out enforcement.

Test:
- STOP -> suppression;
- duplicate STOP -> one business effect;
- HELP -> event;
- START according to policy;
- unknown MO -> normal inbound;
- wrong route -> quarantine/reject;
- tenant spoof field ignored/denied.

## Phase 15 — Middleware event outbox

Reuse existing Outbox signing/retry infrastructure.

Emit schema-versioned events:
- sms.submitted
- sms.sent
- sms.delivered
- sms.failed
- sms.expired
- sms.undeliverable
- sms.inbound.received
- sms.opted_out
- sms.opted_in
- sms.help_requested

Keep:
- HMAC;
- timestamp;
- event ID;
- correlation;
- idempotency;
- bounded retries;
- DLQ.

Do not write Odoo directly.

If Middleware receiver contract changes are required, open a separate governed follow-up PR in the appropriate Codestra repo; do not weaken Telnexa to fit an unsafe receiver.

## Phase 16 — Customer webhooks

Reuse Webhook/WebhookDelivery.

Webhooks are downstream notifications, not carrier submission dependencies.

Require:
- SSRF protection;
- redirect denial;
- HMAC;
- timestamp/event ID;
- retries;
- DLQ;
- manual replay with audit;
- no duplicate webhook job per unique event/subscription.

## Phase 17 — Provider configuration/credentials

Extend Provider or add versioned provider connector config.

Secrets are references only.

Do not add an API that returns/stores raw carrier/Jasmin passwords.

Provision/rotate secrets through restricted Telnexa operator actions.

If operator actions are missing, implement source-side operator extension with exact allowlisted actions and separate governed review.

## Phase 18 — Production gates

Default:

`TELNEXA_PRODUCTION_SMS_ENABLED=false`

Provider routing false until approved.

Add durable canary gate with exact tenant/sender/destination and maximum submissions.

A single-destination canary must be impossible to widen via request body.

Tests:
- wrong tenant denied;
- wrong sender denied;
- wrong destination denied;
- second message beyond max denied;
- gate expiry denied;
- gate closure immediate.

## Phase 19 — Health/readiness/version/metrics

Extend:
- `/healthz`
- `/readyz`
- `/version`
- protected `/metrics`

Readiness should reflect required DB/worker/queue state but must not require a live carrier bind when production routing is intentionally disabled.

Add metrics listed in the contract.

Add alerts for queue age, open circuit, provider errors, unknown submissions, unmatched DLRs, billing drift and worker heartbeats.

## Phase 20 — Compose/runtime

Add non-root services:
- sms-dispatch-worker
- sms-provider-event-worker
- sms-reconciliation-worker

No host ports.

Give each only necessary networks.

Commercial API must not gain Docker socket or Jasmin admin access.

Jasmin remains private.

RabbitMQ/Redis/PostgreSQL remain private.

Preserve Telnexa/Klyrow shared-host separation and do not mutate Klyrow resources.

## Phase 21 — Deprecate direct raw Jasmin production use

After adapter E2E is proven:

1. identify all Middleware/current callers of `sms.telnexa.co/send`;
2. migrate them to the governed Telnexa API;
3. remove Jasmin credentials from Middleware secret scope;
4. remove/privatize public raw `/send` route;
5. verify `DIRECT_MIDDLEWARE_TO_JASMIN=DENIED`;
6. verify Telnexa adapter still submits successfully in sandbox/canary.

Do not remove the compatibility path before every authorized caller is migrated.

## Phase 22 — Backup/restore

Before production mutation:
- backup billing PostgreSQL;
- backup Jasmin config;
- backup non-secret provider config;
- capture Compose/release manifests;
- checksum;
- encrypt/off-host according to existing policy.

Run isolated restore including new adapter tables.

Verify:
- Message;
- Reservation;
- DispatchJob;
- Attempt;
- route decision;
- provider event inbox;
- reconciliation cases;
- billing ledger/usage;
- RLS.

## Phase 23 — Security certification

Run:
- tenant IDOR;
- RLS cross-tenant;
- wrong OIDC issuer;
- wrong audience;
- wrong azp;
- wrong scopes;
- provider callback replay;
- signature method/path/body tamper;
- webhook SSRF;
- public-port scan;
- secret scan;
- dependency audit;
- container scan;
- log/evidence secret inspection;
- direct Middleware->Jasmin negative test.

Require zero fixable HIGH/CRITICAL runtime findings unless an independently reviewed reachability exception with no available fix is documented.

## Phase 24 — Synthetic load/failure

Use fake/sandbox provider only.

Test:
- 100, 1,000 and bounded higher synthetic submissions;
- concurrent API acceptance;
- provider TPS throttling;
- multiple dispatch workers;
- worker termination and lease recovery;
- Redis restart;
- RabbitMQ restart;
- DB restart/failover according to available architecture;
- Middleware outage;
- webhook outage;
- fake Jasmin outage;
- slow/ambiguous fake Jasmin.

Measure P50/P95/P99, queue age, errors, DB pool, worker utilization.

No load test may target a live carrier.

## Phase 25 — Provider sandbox

Only when legitimate sandbox credentials exist.

Use explicit authorized destinations.

Test:
- bind/connect health;
- one MT;
- DLR;
- MO if provider supports;
- GSM-7;
- Unicode;
- multipart;
- invalid destination;
- throttle behavior;
- definitive rejection;
- failover only using a safe controlled case.

Do not claim production certification from simulator tests.

## Phase 26 — Single real canary

Only after human/provider authorization.

Open a server-side canary for:
- exact tenant;
- exact sender;
- exact destination;
- max 1.

Run one message.

Require:

```text
API_ACCEPT=PASS
BILLING_RESERVATION=PASS
DISPATCH=PASS
JASMIN_SUBMISSION=PASS
PROVIDER_MESSAGE_ID=PASS
DLR=PASS
MESSAGE_STATE=PASS
USAGE=1
DUPLICATE_BILLING_EFFECTS=0
MIDDLEWARE_EVENT=PASS
CUSTOMER_WEBHOOK=PASS_OR_NOT_CONFIGURED
CANARY_MAX_ENFORCEMENT=PASS
```

Close canary immediately after evidence.

If live provider credentials/destination authorization are unavailable, terminal status is software-ready/provider-gated, not production certified.

## GitHub/release governance

For implementation:

1. source branch;
2. tests;
3. migration;
4. docs/OpenAPI;
5. CI;
6. secret scan;
7. dependency scan;
8. container scan;
9. immutable artifact/SBOM/provenance;
10. independent review;
11. normal merge;
12. restricted operator deployment;
13. exact runtime readback.

Do not self-approve.
Do not bypass branch protection.
Do not admin-merge merely to finish the mission.

If a PR head changes, exact-head approval must be fresh where repository policy requires it.

## Failure policy

Internally fixable:

`FIX -> RETEST -> CONTINUE`

Do not stop for ordinary:
- test failures;
- lint failures;
- migration bugs;
- response parser bugs;
- fake provider failures;
- Compose bugs;
- missing indexes;
- worker crash;
- route engine defects.

Stop only for a real external gate such as:
- independent reviewer approval;
- carrier credentials;
- carrier IP allowlist/provider-side change;
- sender registration approval;
- destination authorization;
- external DNS/provider control;
- legal/compliance decision;
- infrastructure purchase.

Never invent external credentials or approval.

## Required final report

```text
BASE_MAIN_SHA=
IMPLEMENTATION_SHA=
IMPLEMENTATION_PR=

DATABASE_MIGRATION=
RLS=

COMMERCIAL_API_DURABLE_ACCEPTANCE=
SIMULATOR_SEPARATED_FROM_PRODUCTION=

JASMIN_ADAPTER=
JASMIN_CREDENTIAL_ISOLATION=
ROUTE_ENGINE=
PROVIDER_CIRCUIT_BREAKER=
PROVIDER_TPS_ENFORCEMENT=
SAFE_FAILOVER=
AMBIGUOUS_SUBMISSION_HANDLING=

DISPATCH_QUEUE=
DISPATCH_WORKER_RECOVERY=
PROVIDER_EVENT_WORKER_RECOVERY=
RECONCILIATION_WORKER=

DLR_INGEST=
DLR_IDEMPOTENCY=
DLR_OUT_OF_ORDER=
MO_INGEST=
STOP_HELP_COMPLIANCE=

BILLING_RESERVATION=
BILLING_FINALIZATION=
BILLING_RELEASE=
DUPLICATE_BILLING_EFFECTS=
USAGE_PER_BILLABLE_MESSAGE=

MIDDLEWARE_EVENT_OUTBOX=
MIDDLEWARE_RETRY_DLQ=
CUSTOMER_WEBHOOKS=

TENANT_ISOLATION=
PROVIDER_EVENT_REPLAY=
DIRECT_MIDDLEWARE_TO_JASMIN=
RAW_JASMIN_PUBLIC=
POSTGRESQL_PUBLIC=
REDIS_PUBLIC=
RABBITMQ_PUBLIC=
DOCKER_API_PUBLIC=

PRODUCTION_CANARY_GATE=
PROVIDER_SANDBOX=
SINGLE_REAL_CANARY=

SECRET_SCAN=
DEPENDENCY_SCAN=
CONTAINER_SCAN=
BACKUP=
ISOLATED_RESTORE=
ROLLBACK_READY=

EVIDENCE=
EVIDENCE_SHA256=
FINAL_STATUS=
```

## Terminal statuses

If all software, security, durability, simulator/fake-provider and deployment gates pass but external provider/live authorization is not available:

`FINAL_STATUS=TELNEXA_PRODUCTION_SMS_ADAPTER_SOFTWARE_READY_PROVIDER_GATED`

If legitimate carrier credentials, route authorization and one bounded real canary all pass:

`FINAL_STATUS=TELNEXA_PRODUCTION_SMS_ADAPTER_CERTIFIED`

Never claim the second state without direct live evidence.
