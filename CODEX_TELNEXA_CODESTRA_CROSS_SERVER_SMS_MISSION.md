# CODEX MISSION — TELNEXA + CODESTRA MIDDLEWARE CROSS-SERVER SMS IMPLEMENTATION

## Mission

Implement the complete Telnexa production SMS adapter, update Codestra Middleware on Server A to use the governed Telnexa commercial API instead of raw Jasmin, then certify the two-server SMS path end to end.

Do not return another architecture plan. The architecture authority already exists in this repository.

## Mandatory reading

Read completely before modification:

1. `docs/TELNEXA_PRODUCTION_SMS_ADAPTER_BLUEPRINT.md`
2. `config/telnexa-production-sms-adapter-contract.yaml`
3. `CODEX_TELNEXA_PRODUCTION_SMS_ADAPTER_MISSION.md`
4. `docs/TELNEXA_CODESTRA_MIDDLEWARE_INTEGRATION_BLUEPRINT.md`
5. `docs/TELNEXA_CROSS_SERVER_SMS_E2E_CERTIFICATION_BLUEPRINT.md`
6. `config/telnexa-codestra-cross-server-sms-contract.yaml`

## Systems

### Host 1 — Communications/Telnexa

- Public: `37.27.128.39`
- Private: `10.40.0.4`
- Responsibility: Telnexa commercial SMS API, Telnexa billing, provider adapter, Jasmin, SMPP, provider events.

### Host 2 — Codestra Control Plane / Server A

- Public: `65.109.65.169`
- Private: `10.40.0.1`
- Responsibility: Caddy, Kong, Keycloak integration, Codestra Middleware, governed Odoo/n8n integration.

### Voice host

- Public: `65.21.67.207`
- Private: `10.40.0.2`
- Not an SMS provider target. Do not route Klyrow/Telnexa SMS provider traffic here.

## Repositories

Re-read live state before acting. Expected ownership:

- `appolon1908-hue/telnexa`
- `Codestra-SRL/codestra-middleware`
- `appolon1908-hue/codestra-production-platform`

If live source ownership differs, document the discovered authoritative source and use it instead of inventing a duplicate.

## Global safety

Keep all real external SMS delivery disabled during software implementation.

Require effective fail-closed controls including the canonical discovered equivalents of:

```text
TELNEXA_PRODUCTION_SMS_ENABLED=false
LIVE_SMS_DELIVERY=false
SMS_PRODUCTION_ENABLED=false
ENABLE_EXTERNAL_DELIVERY=false
```

Do not modify Klyrow/Postal email state except read-only regression checks.

Do not modify VICIdial/voice except read-only network regression checks.

Do not send bulk or campaign SMS during this mission.

Do not create provider credentials, sender approvals, destination authorizations, or carrier routes that were not legitimately supplied.

## Phase 0 — Release leader and concurrency

Before mutation discover:

- active Codex sessions;
- active Telnexa deployment/operator actions;
- active Server A release/operator actions;
- active GitHub PR/release workflows;
- active DB migrations;
- active network changes.

Establish one mutation leader for this mission.

Require:

```text
CONCURRENT_SMS_PRODUCTION_MUTATION=NO
SMS_RELEASE_LEADER=PASS
```

Other sessions may inspect and prepare but must not race runtime mutation.

## Phase 1 — Re-read current source and gap classification

For every component classify:

```text
REUSE
EXTEND
NEW
DEPRECATE
BLOCKED_EXTERNAL
```

Do not duplicate existing Message, MessageEvent, billing reservation, wallet, ledger, Usage, Provider, Route, Sender, Contact/consent, webhook, Middleware outbox, RLS, OIDC/API key, or existing platform outbox/inbox capabilities.

Record current SHAs and open PRs for all three repositories.

## Phase 2 — Implement Telnexa provider adapter on Host 1

Create a fresh implementation branch from current protected/default source; do not implement directly in the planning branch.

Replace production use of simulator-only submission behind Telnexa commercial send with the durable provider adapter architecture.

Required flow:

```text
Telnexa API
-> authorization/tenant/compliance
-> billing reservation
-> Message
-> sms_dispatch_job
-> route decision
-> dispatch worker
-> provider-neutral adapter
-> JasminHttpAdapter
-> private Jasmin
-> carrier
```

Implement/reuse logical records:

- `sms_dispatch_jobs`
- `sms_dispatch_attempts`
- `sms_route_decisions`
- `sms_provider_event_inbox`
- `sms_provider_event_attempts`
- `sms_reconciliation_cases`
- `sms_production_canary_gates`

Implement workers:

- SMS dispatch worker;
- provider-event worker;
- reconciliation worker.

Do not expose raw Jasmin credentials through commercial APIs.

## Phase 3 — Provider submission state machine

Require deterministic states including:

```text
ACCEPTED
QUEUED
PROCESSING
SUBMITTED
DELIVERED
FAILED
EXPIRED
UNDELIVERABLE
SUBMISSION_UNKNOWN
```

The critical rule is:

```text
ambiguous provider/Jasmin timeout
-> SUBMISSION_UNKNOWN
-> reconciliation
```

Never:

```text
ambiguous timeout
-> blindly send through another provider
```

Require:

```text
BLIND_FAILOVER_AFTER_AMBIGUOUS_SUBMISSION=DENIED
```

## Phase 4 — Telnexa billing correctness

Telnexa remains authoritative for segment calculation and SMS rates.

Flow:

```text
accept logical message
-> calculate GSM-7/UCS-2 segments
-> snapshot provider/sell rates
-> reserve
-> create dispatch job
-> provider definitive acceptance
-> finalize once
-> usage once
```

Definitive pre-submit failure may release reservation.

Ambiguous submission must not cause release + second provider charge until reconciliation proves outcome.

Require:

```text
BILLING_RESERVATION=PASS
RATE_SNAPSHOT=PASS
BILLING_FINALIZE_EXACTLY_ONCE=PASS
USAGE_EXACTLY_ONCE=PASS
DUPLICATE_BILLING=0
```

## Phase 5 — Telnexa DLR/MO durable inbox

Provider/Jasmin callbacks must enter Telnexa durable state first.

Flow:

```text
Jasmin/provider relay
-> authenticated event
-> durable provider event inbox
-> schema/idempotency
-> DLR/MO processor
-> message/compliance/billing state
-> Telnexa integration outbox
-> Middleware
```

Provider event cannot choose tenant ownership from an arbitrary callback field.

MO STOP/HELP processing must remain local and durable during Server A outage.

Require:

```text
DLR_DURABLE_INBOX=PASS
MO_DURABLE_INBOX=PASS
PROVIDER_EVENT_REPLAY=DEDUPLICATED
STOP_LOCAL_DURABILITY=PASS
```

## Phase 6 — Telnexa source tests and governed release

Add:

- migrations;
- unit tests;
- provider adapter contract tests;
- billing/idempotency tests;
- ambiguous submission tests;
- DLR/MO tests;
- security tests;
- Compose/runtime tests;
- secret scan;
- dependency scan;
- container scan.

Submit through normal review/CI. Do not self-approve protected requirements.

Produce immutable release evidence before deployment.

## Phase 7 — Host 1 backup/restore/deployment

Before Telnexa runtime mutation:

- fresh encrypted backup;
- checksum validation;
- isolated restore;
- rollback plan;
- shared-host Klyrow baseline.

Deploy through the existing restricted Telnexa operator or the governed equivalent.

Keep external SMS disabled.

Require:

```text
TELNEXA_BACKUP=PASS
TELNEXA_BACKUP_VALIDATION=PASS
TELNEXA_RESTORE_TEST=PASS
TELNEXA_ROLLBACK_READY=PASS
TELNEXA_RUNTIME_RELEASE_MATCH=PASS
KLYROW_PRECHANGE_BASELINE=PASS
```

## Phase 8 — Implement Server A Middleware client

On `65.109.65.169`, re-read live source in:

- `Codestra-SRL/codestra-middleware`
- `appolon1908-hue/codestra-production-platform`

Do not rebuild Telnexa logic inside Middleware.

Implement/extend the canonical Middleware SMS endpoint. Reuse an existing canonical route if present; otherwise preferred endpoint is:

```text
POST /v1/messages/sms
```

Middleware responsibilities:

- Keycloak/Kong authentication;
- authoritative tenant resolution;
- `sms.send` authorization;
- request validation;
- request/correlation IDs;
- tenant-scoped idempotency;
- call Telnexa commercial API over private governed interface;
- persist Telnexa message ID;
- expose tenant-safe status/timeline;
- process Telnexa events through durable inbox/outbox/reconciliation.

Middleware is not responsible for direct Jasmin transport.

## Phase 9 — Remove Middleware raw Jasmin dependency

Inventory and eliminate/deprecate production references to:

- `sms.telnexa.co/send` as a raw Jasmin endpoint;
- Jasmin API username/password in Middleware;
- direct `10.40.0.4:1401` usage;
- jCli access;
- SMPP provider credentials;
- provider-specific DLR callback credentials.

After new client passes software E2E, enforce network/config denial of the obsolete direct path where safe.

Require:

```text
MIDDLEWARE_DIRECT_JASMIN_CREDENTIALS=ABSENT
MIDDLEWARE_TO_JASMIN_DIRECT_PATH=DENIED
MIDDLEWARE_JASMIN_FALLBACK=DENIED
```

## Phase 10 — Server A Telnexa event receiver

Implement/reuse protected event receiver.

Preferred logical endpoint if no canonical route exists:

```text
POST /v1/internal/events/telnexa/sms
```

Require:

- private/protected transport;
- mTLS where current platform contract uses it;
- Telnexa service identity/HMAC as governed;
- timestamp check;
- event ID replay protection;
- schema version;
- body size limit;
- authoritative tenant/message resolution;
- durable persistence before downstream processing;
- retry/DLQ/reconciliation.

## Phase 11 — Server A source tests and releases

Add changed-path tests for:

- no token;
- wrong issuer;
- wrong audience;
- wrong scope;
- tenant spoof;
- cross-tenant message lookup;
- same idempotency request;
- altered idempotency body;
- Telnexa unavailable;
- forged/replayed Telnexa event;
- direct Jasmin bypass denied;
- SMS external flags fail-closed.

Send Middleware and platform changes through their normal protected CI/review/release flows.

## Phase 12 — Server A backup/restore/deployment

Before runtime mutation:

- fresh backup of changed Middleware/control-plane durable state;
- validation;
- isolated restore;
- rollback proof;
- pre/post listener inventory.

Deploy exact signed release through restricted operator.

Keep SMS external delivery disabled.

Require:

```text
SERVER_A_BACKUP=PASS
SERVER_A_BACKUP_VALIDATION=PASS
SERVER_A_RESTORE_TEST=PASS
SERVER_A_ROLLBACK_READY=PASS
MIDDLEWARE_RUNTIME_RELEASE_MATCH=PASS
```

## Phase 13 — Cross-server network contract

Require allowed:

```text
10.40.0.1 -> 10.40.0.4 Telnexa governed API
10.40.0.4 -> 10.40.0.1 Telnexa SMS event receiver
```

Require denied after migration:

```text
10.40.0.1 -> Jasmin admin/jCli
10.40.0.1 -> raw Jasmin provider submission bypass
10.40.0.1 -> Telnexa billing DB
public -> PostgreSQL/Redis/RabbitMQ/Jasmin admin
```

Do not guess ports; discover the live listener/process/Compose ownership and enforce the intended service boundary.

## Phase 14 — Cross-server simulator/private sink E2E

Run exactly one synthetic SMS through:

```text
Keycloak identity
-> Kong
-> Middleware
-> Telnexa API
-> billing reserve
-> dispatch job
-> simulator/private sink
-> finalize
-> Telnexa event outbox
-> Middleware receiver
-> readback/reconciliation
```

No live carrier.

Require:

```text
SYNTHETIC_SMS_ACCEPTED=PASS
MIDDLEWARE_TO_TELNEXA=PASS
TELNEXA_DISPATCH_JOB=PASS
BILLING_FINALIZE=PASS
USAGE_RECORDS=1
TELNEXA_TO_MIDDLEWARE_EVENT=PASS
RECONCILIATION=PASS
DUPLICATE_SMS=0
DUPLICATE_BILLING=0
```

## Phase 15 — Idempotency E2E

Repeat the same request with the same key.

Require one logical message and no second dispatch/billing.

Reuse key with changed request.

Require conflict/denial.

```text
SAME_REQUEST_REPLAY=ONE_LOGICAL_MESSAGE
ALTERED_REQUEST_REPLAY=DENIED
DUPLICATE_DISPATCH=0
DUPLICATE_USAGE=0
```

## Phase 16 — DLR E2E

Inject one authenticated synthetic/sandbox DLR, then replay it.

Require:

```text
DLR_E2E=PASS
DLR_REPLAY=DEDUPLICATED
DUPLICATE_DLR_BUSINESS_EFFECTS=0
```

## Phase 17 — MO + STOP E2E

Inject an authorized synthetic/sandbox MO and then STOP.

Temporarily make the Middleware test receiver unavailable only in a controlled lane.

Telnexa must persist STOP and enforce suppression before Middleware returns.

Require:

```text
MO_E2E=PASS
STOP_LOCAL_DURABILITY=PASS
STOP_WITH_SERVER_A_DOWN=PASS
MARKETING_AFTER_STOP=DENIED
STOP_EVENT_RECONCILIATION=PASS
```

## Phase 18 — Failure recovery

Controlled test lanes only.

Test:

- Telnexa API unavailable before acceptance;
- Middleware receiver unavailable after Telnexa event creation;
- dispatch worker restart;
- event worker restart;
- Redis/broker outage where architecture uses them;
- ambiguous provider submission.

Require:

```text
TELNEXA_OUTAGE_FAIL_CLOSED=PASS
MIDDLEWARE_OUTAGE_EVENT_DURABILITY=PASS
WORKER_RECOVERY=PASS
EVENT_RECOVERY=PASS
BLIND_FAILOVER=DENIED
DOUBLE_SEND=0
DOUBLE_BILL=0
```

## Phase 19 — Security negative matrix

Require:

```text
NO_TOKEN=DENIED
WRONG_ISSUER=DENIED
WRONG_AUDIENCE=DENIED
WRONG_SCOPE=DENIED
TENANT_SPOOF=DENIED
CROSS_TENANT_ACCESS=DENIED
UNAPPROVED_SENDER=DENIED
SUPPRESSED_MARKETING=DENIED
FORGED_PROVIDER_EVENT=DENIED
STALE_PROVIDER_EVENT=DENIED
DIRECT_JASMIN_BYPASS=DENIED
POSTGRES_PUBLIC=DENIED
REDIS_PUBLIC=DENIED
RABBITMQ_PUBLIC=DENIED
JASMIN_ADMIN_PUBLIC=DENIED
SECRET_LEAKS=0
```

## Phase 20 — Controlled synthetic load

Only after both hosts have passing backup/restore/rollback.

Use simulator/private sink.

Measure API latency, queue age, worker utilization, DB/Redis/broker pressure, billing latency, event lag and reconciliation lag.

No DoS/flooding and no live-carrier bulk load.

Require:

```text
SYNTHETIC_SMS_LOAD=PASS
BACKPRESSURE=PASS
RATE_LIMITS=PASS
```

## Phase 21 — Klyrow/shared-host regression

On `37.27.128.39` verify the SMS implementation did not mutate Klyrow/Postal/email behavior.

Require:

```text
KLYROW_HEALTH=PASS
POSTAL_HEALTH=PASS
KLYROW_CONFIG_UNCHANGED_OR_GOVERNED=PASS
EMAIL_DELIVERY_GATE_UNCHANGED=PASS
```

## Phase 22 — Software certification

If carrier credentials/routes or an authorized destination are unavailable, stop only after all software/cross-server gates pass.

Terminal:

```text
FINAL_STATUS=TELNEXA_CODESTRA_SMS_CROSS_SERVER_SOFTWARE_CERTIFIED
```

This is a legitimate successful software state, not permission to claim real carrier delivery.

## Phase 23 — Provider sandbox

If legitimate sandbox credentials and an authorized sandbox destination exist, run one bounded provider sandbox test:

- bind/connect;
- one MT;
- DLR;
- one MO if supported;
- multipart/Unicode where sandbox supports;
- throttle handling;
- reconciliation.

Do not invent credentials or destinations.

## Phase 24 — Exactly one real production SMS canary

Only after explicit owner/operator authorization of:

- exact destination;
- exact sender;
- carrier/provider route;
- content;
- time window;
- expected cost.

Set durable canary maximum to exactly one real logical delivery.

Do not send a second canary because a DLR is delayed.

Require:

```text
CANARY_RESERVED=1
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

Then:

```text
FINAL_STATUS=TELNEXA_PRODUCTION_SMS_ADAPTER_CERTIFIED
```

## Failure policy

Do not bypass GitHub governance.

Do not self-approve protected review requirements.

Do not give Server A Jasmin credentials.

Do not restore direct Middleware -> Jasmin as a fallback.

Do not blindly fail over ambiguous provider submissions.

Do not send uncontrolled SMS.

Do not touch Klyrow/Postal except read-only regression or explicitly governed shared-host dependency work.

For internally fixable failures:

```text
FIX
-> TEST
-> RETEST
-> CONTINUE
```

Stop only at a genuine external governance/provider authorization blocker or a truthful terminal certification state.

## Final report

```text
TELNEXA_ADAPTER=
TELNEXA_RUNTIME_RELEASE_MATCH=
TELNEXA_BACKUP=
TELNEXA_RESTORE=
TELNEXA_ROLLBACK=

MIDDLEWARE_TELNEXA_CLIENT=
MIDDLEWARE_RUNTIME_RELEASE_MATCH=
SERVER_A_BACKUP=
SERVER_A_RESTORE=
SERVER_A_ROLLBACK=

MIDDLEWARE_DIRECT_JASMIN_CREDENTIALS=
DIRECT_JASMIN_BYPASS=

SYNTHETIC_SMS_E2E=
IDEMPOTENCY_E2E=
DLR_E2E=
MO_E2E=
STOP_E2E=

AMBIGUOUS_SUBMISSION=
BLIND_FAILOVER=
DUPLICATE_SMS=
DUPLICATE_BILLING=

BILLING_RECONCILIATION=
CROSS_SERVER_TRACEABILITY=
SECURITY_NEGATIVE_MATRIX=
SYNTHETIC_SMS_LOAD=

KLYROW_REGRESSION=

PROVIDER_SANDBOX=
REAL_CANARY_AUTHORIZED=
REAL_CANARY_RESULT=

EVIDENCE=
EVIDENCE_SHA256=
FINAL_STATUS=
```
