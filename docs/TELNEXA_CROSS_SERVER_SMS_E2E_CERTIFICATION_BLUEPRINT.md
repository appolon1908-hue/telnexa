# Telnexa ↔ Codestra Cross-Server SMS E2E Certification Blueprint

## Purpose

This document defines the production-safe end-to-end certification between:

- Communications/Telnexa host `37.27.128.39` / `10.40.0.4`.
- Codestra Control Plane / Server A `65.109.65.169` / `10.40.0.1`.

It is the final integration/certification step after the Telnexa production adapter and the Codestra Middleware Telnexa client/event receiver are software-ready.

No bulk traffic, uncontrolled SMS, or customer-wide production activation is authorized by this document.

## Certification target

Software-only/simulator target:

`TELNEXA_CODESTRA_SMS_CROSS_SERVER_SOFTWARE_CERTIFIED`

Production target after legitimate provider credentials, approved route/sender, and one explicitly authorized destination canary:

`TELNEXA_PRODUCTION_SMS_ADAPTER_CERTIFIED`

## Required retained safety state

Before any test:

```text
TELNEXA_PRODUCTION_SMS_ENABLED=false
LIVE_SMS_DELIVERY=false
SMS_PRODUCTION_ENABLED=false
PRODUCTION_DIALING=false
LIVE_PSTN_DIALING=false
EMAIL_UNRELATED=true/no-change
```

Use the actual canonical live variable names. Required SMS external-delivery flags must be false. Do not reinterpret a missing required flag as a pass unless source tests explicitly define missing as fail-closed.

## Preflight evidence

### Server A

Require:

- restricted operator pass;
- current backup pass;
- backup validation pass;
- isolated restore pass;
- rollback ready;
- Caddy healthy;
- Kong healthy;
- canonical Keycloak issuer healthy;
- Middleware healthy;
- PostgreSQL healthy/private;
- Redis/message bus healthy where used;
- Odoo/n8n regression healthy;
- no unexpected public listener.

### Telnexa host

Require:

- restricted Telnexa operator pass;
- encrypted backup pass;
- backup validation pass;
- isolated restore pass;
- rollback ready;
- Telnexa API healthy;
- Telnexa billing API/worker/database healthy;
- Jasmin healthy;
- Redis/RabbitMQ healthy/private;
- provider-event relay healthy;
- no unexpected public listener;
- Klyrow regression unchanged.

If backup/restore/rollback is not current enough for the changed runtime, refresh it before failure tests or a real canary.

## Release consensus

Record for every changed component:

- repository;
- protected/source SHA;
- immutable image digest;
- CI run;
- independent approval;
- SBOM/provenance/signature where used;
- running digest;
- `/version` response.

Require source/runtime consensus. Never certify a newer/older untracked image merely because health is green.

## Connectivity matrix

Expected allowed paths:

| Source | Destination | Purpose | Expected |
|---|---|---|---|
| Server A `10.40.0.1` | Telnexa governed private API `10.40.0.4` | SMS commercial requests | PASS |
| Telnexa `10.40.0.4` | Server A protected event receiver `10.40.0.1` | DLR/MO/billing/compliance events | PASS |
| Telnexa adapter | Jasmin private API | provider submission | PASS |
| Jasmin | carrier SMPP sandbox/production route | carrier execution | GATED/approved only |

Expected denied paths after migration:

| Source | Destination | Expected |
|---|---|---|
| Server A | Jasmin jCli/admin | DENIED |
| Server A | Jasmin raw transport API bypass | DENIED |
| Server A | Telnexa billing DB | DENIED |
| public Internet | Jasmin HTTP/SMPP management | DENIED |
| public Internet | Telnexa PostgreSQL/Redis/RabbitMQ | DENIED |
| customer browser | provider/Jasmin credentials | DENIED |

## Test lane levels

Certification proceeds in strict order.

### Lane A — unit/contract

No network submission.

### Lane B — cross-server simulator/private sink

Uses real authentication, two-server path, durable databases, outboxes, workers, reconciliation, but no carrier.

### Lane C — provider sandbox or carrier test environment

Only if legitimate provider sandbox credentials exist. Destination must be explicitly approved.

### Lane D — exactly one real production canary

Only after all prior lanes pass and the owner/operator explicitly authorizes the destination, sender, time window, and carrier route.

Do not jump directly to Lane D.

## Lane A: schema and cryptographic contracts

Verify exact request/response/event schemas between repos.

Required:

- Middleware send schema matches Telnexa commercial API;
- Telnexa event envelope matches Middleware receiver;
- enum values have deterministic mapping;
- timestamp format normalized;
- event ID required;
- message ID required;
- tenant ID never taken from provider callback alone;
- correlation ID preserved end-to-end;
- HMAC canonicalization vectors match if HMAC is used;
- mTLS certificate chain/SAN/identity match if mTLS is used;
- wrong signature rejected;
- expired timestamp rejected;
- wrong source rejected;
- unsupported schema version rejected fail-closed.

Require:

```text
SMS_REQUEST_SCHEMA_CONSENSUS=PASS
SMS_EVENT_SCHEMA_CONSENSUS=PASS
SMS_SIGNATURE_VECTOR=PASS
SMS_REPLAY_CONTRACT=PASS
```

## Lane B: synthetic outbound E2E

Run exactly one synthetic message using an internal fixture and non-provider sink.

Flow:

```text
Keycloak service/test identity
-> Kong
-> Middleware POST /v1/messages/sms
-> Middleware idempotency/authz
-> Telnexa POST /api/v1/messages
-> tenant/sender/compliance policy
-> billing reservation
-> durable dispatch job
-> simulator/private sink adapter
-> accepted receipt
-> billing finalize
-> Telnexa event outbox
-> Server A event receiver
-> Middleware inbox
-> optional Odoo fixture/readback
-> reconciliation
```

Require:

```text
SYNTHETIC_SMS_ACCEPTED=PASS
MIDDLEWARE_TO_TELNEXA=PASS
TELNEXA_RESERVATION=PASS
TELNEXA_DISPATCH_JOB=PASS
SIMULATOR_PROVIDER_ACCEPT=PASS
BILLING_FINALIZE=PASS
USAGE_RECORDS=1
TELNEXA_TO_MIDDLEWARE_EVENT=PASS
MIDDLEWARE_EVENT_PERSISTENCE=PASS
SYNTHETIC_RECONCILIATION=PASS
DUPLICATE_SMS=0
DUPLICATE_BILLING=0
```

## Idempotency test

Repeat the exact original request with the same `Idempotency-Key`.

Expected:

- same logical Middleware request;
- same Telnexa logical message;
- no new dispatch job requiring provider execution;
- no second reservation/finalization;
- one usage record.

Then reuse the same key with changed content/destination/sender.

Expected: `409` or canonical conflict.

Require:

```text
SAME_REQUEST_REPLAY=ONE_LOGICAL_MESSAGE
ALTERED_REQUEST_REPLAY=DENIED
DUPLICATE_DISPATCH=0
DUPLICATE_USAGE=0
```

## DLR E2E

Inject one authenticated synthetic/provider-sandbox DLR for the accepted test message.

Flow:

```text
Jasmin/provider adapter
-> Telnexa relay
-> provider event inbox
-> DLR processor
-> MessageEvent
-> billing/status reconciliation
-> integration outbox
-> Middleware receiver
-> tenant-scoped readback
```

Replay the same DLR event ID.

Require:

```text
DLR_FIRST_ACCEPT=PASS
DLR_DUPLICATE=DEDUPLICATED
MESSAGE_STATUS_UPDATE=PASS
MIDDLEWARE_DLR_READBACK=PASS
DUPLICATE_DLR_BUSINESS_EFFECTS=0
```

## MO / inbound E2E

Use a synthetic/provider-sandbox inbound message assigned to an approved tenant number/route.

Require Telnexa to resolve tenant/destination from its own records.

Flow:

```text
carrier/Jasmin MO
-> Telnexa provider inbox
-> tenant/number resolution
-> local compliance parser
-> durable inbound message
-> Telnexa integration outbox
-> Middleware receiver
-> governed downstream fixture
```

Require:

```text
MO_TENANT_RESOLUTION=PASS
MO_PERSISTENCE=PASS
MO_TO_MIDDLEWARE=PASS
MO_DUPLICATE=DEDUPLICATED
```

## STOP compliance E2E

Run an authorized synthetic inbound `STOP`/configured opt-out keyword.

Server A may be deliberately unavailable in an isolated/controlled test lane.

Telnexa must still:

1. authenticate provider event;
2. persist MO;
3. update local suppression/consent state;
4. generate compliance event in durable outbox;
5. reject subsequent marketing SMS for that tenant/recipient;
6. deliver the compliance event to Middleware after Server A recovery.

Require:

```text
STOP_LOCAL_DURABILITY=PASS
STOP_WITH_SERVER_A_DOWN=PASS
MARKETING_AFTER_STOP=DENIED
STOP_EVENT_RETRY=PASS
STOP_EVENT_RECONCILIATION=PASS
```

## HELP/START policy

Test configured `HELP` and `START`/opt-in semantics only according to legal/country policy. Do not assume `START` automatically establishes lawful marketing consent unless the configured policy explicitly permits it and records proof.

Require deterministic local event generation and audit.

## Middleware outage test

After Telnexa has an accepted provider event, make the Middleware test receiver temporarily unavailable in a controlled lane.

Verify:

- Telnexa event remains durable;
- retry/backoff occurs;
- no provider SMS is re-sent;
- no billing duplication;
- event reaches Middleware after recovery;
- reconciliation closes drift.

Require:

```text
MIDDLEWARE_OUTAGE_EVENT_DURABILITY=PASS
EVENT_RETRY=PASS
EVENT_DLQ_OR_RECOVERY_POLICY=PASS
PROVIDER_RESEND_DURING_MIDDLEWARE_OUTAGE=0
BILLING_DUPLICATION=0
```

## Telnexa outage test

Make the Telnexa governed API temporarily unavailable in a controlled lane before acceptance.

Middleware must:

- keep request state consistent;
- perform bounded retry with same idempotency key or return fail-closed according to contract;
- never switch to raw Jasmin;
- never invent provider acceptance.

Require:

```text
TELNEXA_OUTAGE_FAIL_CLOSED=PASS
MIDDLEWARE_JASMIN_FALLBACK=DENIED
```

## Ambiguous submission test

This is mandatory.

Simulate a provider adapter condition where the network fails after the request may have reached Jasmin/provider but before Telnexa receives a definitive response.

Expected:

```text
message -> SUBMISSION_UNKNOWN
```

Then:

- no immediate alternate provider dispatch;
- reservation not incorrectly released/finalized twice;
- reconciliation waits for provider/Jasmin evidence;
- a subsequent matching DLR/provider receipt can close the case;
- if the provider proves no submission occurred, a governed retry may resume according to policy.

Require:

```text
AMBIGUOUS_SUBMISSION_STATE=PASS
BLIND_FAILOVER=DENIED
DOUBLE_SEND=0
DOUBLE_BILL=0
RECONCILIATION_CLOSE=PASS
```

## Provider routing/failover test

Use simulator or approved provider sandbox.

Verify route ordering using:

- destination/country/prefix;
- sender capability;
- connector health;
- circuit breaker;
- tenant/provider authorization;
- pricing/margin policy;
- throughput/TPS capacity;
- compliance restrictions.

A definitively failed pre-submit attempt may try an approved backup route. An ambiguous submission may not.

Require:

```text
ROUTE_SELECTION=PASS
OPEN_CIRCUIT_EXCLUDED=PASS
DEFINITE_PRESUBMIT_FAILOVER=PASS
AMBIGUOUS_FAILOVER=DENIED
```

## Billing E2E

Verify one logical SMS with N segments:

- deterministic GSM-7/UCS-2 calculation;
- one reservation tied to logical message;
- rate snapshots immutable for that message;
- final charge equals policy for segments sent/accepted according to billing contract;
- one usage record;
- duplicate DLR does not change charge;
- provider failure follows release/finalization rules exactly.

Require:

```text
SEGMENT_ACCOUNTING=PASS
RATE_SNAPSHOT=PASS
RESERVATION=PASS
FINALIZATION=PASS
USAGE=PASS
DUPLICATE_BILLING=0
```

## Security negative matrix

From public and private test positions verify:

- no auth -> send denied;
- wrong Keycloak issuer -> denied;
- wrong audience -> denied;
- wrong scope -> denied;
- tenant spoof -> denied;
- cross-tenant lookup -> denied;
- unapproved sender -> denied;
- suppressed marketing -> denied;
- country-policy denial -> denied;
- raw provider selection injection -> denied;
- direct Jasmin API from Server A -> denied after migration;
- forged provider event -> denied;
- stale provider event timestamp -> denied;
- duplicate provider event -> no duplicate business effect;
- oversized inbound event -> denied;
- Redis/RabbitMQ/PostgreSQL/Jasmin admin ports not public;
- credentials absent from logs/evidence.

Require:

```text
SMS_SECURITY_NEGATIVE_MATRIX=PASS
CROSS_TENANT_ACCESS=DENIED
DIRECT_JASMIN_BYPASS=DENIED
SECRET_LEAKS=0
```

## Observability E2E

Verify dashboards/metrics can correlate a test by message ID and correlation ID across:

- Middleware request;
- Telnexa acceptance;
- billing reservation;
- route decision;
- dispatch attempt;
- provider receipt;
- DLR/MO;
- Middleware event delivery;
- reconciliation.

Never place message body, bearer token, provider secret, phone number plaintext beyond approved logging policy, or DLR callback secret in logs.

Require:

```text
CROSS_SERVER_TRACEABILITY=PASS
SMS_ALERTING=PASS
SMS_METRICS=PASS
SECRET_FREE_EVIDENCE=PASS
```

## Controlled load test

Only after backup/restore/rollback and software E2E pass.

Use synthetic/private sink traffic, not live carrier traffic unless explicitly authorized.

Measure:

- Middleware API RPS/P50/P95/P99;
- Telnexa API latency;
- dispatch queue depth/age;
- worker utilization;
- database connections;
- Redis/broker pressure;
- billing latency;
- event outbox/inbox lag;
- reconciliation lag.

Abort on configured safety thresholds. Do not perform a DoS/flood test.

Require:

```text
SYNTHETIC_SMS_LOAD=PASS
BACKPRESSURE=PASS
RATE_LIMITS=PASS
```

## Real provider/canary gate

A real SMS may be sent only if all of the following are explicitly known and approved:

- provider/carrier identity;
- approved connector/route;
- provider credentials installed through protected secret path;
- sender/sender ID approved for destination jurisdiction;
- exact destination phone number explicitly authorized by the owner;
- exact message content classified as test/canary and compliant;
- test window;
- expected cost;
- rollback/kill-switch ready;
- `MAX_REAL_SMS_DELIVERIES=1` or equivalent durable gate.

No bulk, campaign, or arbitrary recipient is permitted for the first production canary.

## Exactly-one real canary flow

```text
Authorized identity
-> Server A canonical SMS API
-> Telnexa commercial API
-> durable canary reservation
-> billing reservation
-> one dispatch job
-> approved Jasmin/provider route
-> exactly one destination
-> provider acknowledgement
-> DLR
-> Telnexa event processing
-> Middleware event processing
-> billing/usage reconciliation
```

Require:

```text
CANARY_RESERVED=1
PROVIDER_SUBMISSION=PASS
PROVIDER_MESSAGE_ID=KNOWN
REAL_SMS_LOGICAL_MESSAGES=1
REAL_SMS_PROVIDER_SUBMISSIONS=1
REAL_SMS_DELIVERIES<=1
DLR=PASS
MIDDLEWARE_DLR=PASS
USAGE_RECORDS=1
BILLING_RECORDS=1
DUPLICATE_SMS=0
DUPLICATE_BILLING=0
RECONCILIATION=PASS
```

If carrier delivery is delayed, do not send a second canary just because the first DLR has not arrived. Reconcile first.

## Post-canary closure

After the canary:

- close the canary reservation;
- verify no queued second delivery;
- return any temporary production canary gate to its intended post-test state;
- keep bulk/campaign production disabled unless separately authorized;
- preserve evidence.

## Shared-host regression

Because Telnexa and Klyrow share the communications host, verify Klyrow remains unchanged by SMS adapter work.

Require:

```text
KLYROW_HEALTH=PASS
KLYROW_CONFIG_UNCHANGED_OR_GOVERNED=PASS
POSTAL_UNCHANGED=PASS
EMAIL_DELIVERY_GATE_UNCHANGED=PASS
```

SMS work must not mutate Klyrow databases, Postal routing, mail certificates, or email delivery flags.

## Evidence package

Produce a timestamped, secret-free evidence directory containing:

- server identities;
- source SHAs/digests;
- GitHub PR/CI/review evidence;
- backup/restore/rollback evidence;
- network matrix;
- API schemas/hashes;
- auth negative tests;
- synthetic outbound trace;
- DLR trace;
- MO/STOP trace;
- idempotency/replay trace;
- ambiguous submission trace;
- billing/usage reconciliation;
- load test summary;
- Klyrow regression;
- real canary evidence only if authorized;
- SHA256 manifest.

No secrets, full bearer tokens, provider passwords, or private keys in evidence.

## Final matrix

```text
SERVER_A_BACKUP=
SERVER_A_RESTORE=
SERVER_A_ROLLBACK=

TELNEXA_BACKUP=
TELNEXA_RESTORE=
TELNEXA_ROLLBACK=

SMS_REQUEST_SCHEMA_CONSENSUS=
SMS_EVENT_SCHEMA_CONSENSUS=
SMS_SIGNATURE_VECTOR=

MIDDLEWARE_TO_TELNEXA=
TELNEXA_TO_MIDDLEWARE_EVENT=
DIRECT_JASMIN_BYPASS=

SYNTHETIC_SMS_ACCEPTED=
SAME_REQUEST_REPLAY=
ALTERED_REQUEST_REPLAY=

DLR_E2E=
MO_E2E=
STOP_E2E=

TELNEXA_OUTAGE_FAIL_CLOSED=
MIDDLEWARE_OUTAGE_EVENT_DURABILITY=
AMBIGUOUS_SUBMISSION_STATE=
BLIND_FAILOVER=

SEGMENT_ACCOUNTING=
BILLING_RECONCILIATION=
DUPLICATE_SMS=
DUPLICATE_BILLING=

SMS_SECURITY_NEGATIVE_MATRIX=
CROSS_SERVER_TRACEABILITY=
SYNTHETIC_SMS_LOAD=

KLYROW_REGRESSION=

REAL_CANARY_AUTHORIZED=
REAL_CANARY_RESULT=

EVIDENCE=
EVIDENCE_SHA256=
FINAL_STATUS=
```

Software-only success:

`FINAL_STATUS=TELNEXA_CODESTRA_SMS_CROSS_SERVER_SOFTWARE_CERTIFIED`

Production success after legitimate provider/live canary:

`FINAL_STATUS=TELNEXA_PRODUCTION_SMS_ADAPTER_CERTIFIED`
