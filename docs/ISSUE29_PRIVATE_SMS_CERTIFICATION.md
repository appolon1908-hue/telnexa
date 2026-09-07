# Issue 29: private SMS submission and reconciliation

## Source contract

`POST /api/v1/messages` retains the reviewed `X-API-Key`, `X-Tenant-ID`,
`Idempotency-Key`, and `X-Correlation-ID` contract. The request schema is unchanged
apart from bounded database identifiers. New requests persist the original
acceptance projection in `sms_acceptance_receipts` in the same transaction as the
message, reservation, dispatch job, audit and outbox. Exact replay returns the
original response body even after a delivery transition. The PostgreSQL trigger
rejects receipt updates and deletes. Altered semantic payloads return 409.

A legacy message without an original receipt returns
`409 legacy_acceptance_requires_readback` on POST replay; its old acceptance is
not fabricated from current state. Use non-submitting read-back instead:

```text
GET /api/v1/messages/by-idempotency
X-API-Key: <secret-manager injected, never stored here>
X-Tenant-ID: <authenticated tenant>
Idempotency-Key: <original command key>
```

The response identifies `telnexa.sms.readback.v1`, the original request hash,
tenant/key, stable Telnexa message ID, current status/provider ID, correlation,
client/campaign references, and receipt availability. It omits content and phone
numbers. 404 means no durable record was found; it does not authorize a POST,
resubmit or failover. `sms.status.read` grants only this explicit read-back route,
not generic tenant/billing/contacts reads. Existing approved `sms.read` API keys
remain compatible. The independent pending machine-identity PR is not required.

Consumers must compare tenant, idempotency key, canonical request hash and stable
message ID before treating read-back as a match. Hash the normalized SendRequest,
including `category=transactional`, absent `campaign_id=null` and
`client_reference=null`, with Python JSON sorted keys, compact separators and its
default ASCII escaping. A changing carrier message ID is not the durable command
reference. Never use POST as a missing-record reconciliation fallback.

## Admission and interrupted delivery

Admission locks tenant state, enforces local suppression for every currently
supported non-exempt category, and checks tenant ownership, approval and category
for campaign references even when live submission is disabled. Plan TPS is a
sliding one-second acceptance bound; monthly quota counts durable acceptances in
the UTC calendar month. Exact replays consume neither budget. Existing unassigned
tenants have a bounded compatibility ceiling of 100 TPS / 1,000 per month;
explicit plan limits override it and invalid/zero limits deny admission.

The dispatcher commits its claim before contacting any provider. The next phase
locks that claim and rechecks policy/canary gates. An interrupted/expired claim
moves to reconciliation with unknown outcome, retains the reservation and cannot
be automatically requeued. This conservatively includes interruptions before an
actual network write. Authoritative provider read-back is required to resolve it;
there is no automatic guess that a timeout means a failed send. Legacy production
jobs without a receipt are denied before submission. Review all pending legacy
jobs and campaign/suppression state before rollout.

## Provider callbacks: required v2 cutover

API and relay must be deployed as a reviewed matched tuple. New ingress requires
`X-Signature-Version: v2`; do not mix a v1 relay with a v2 API. The HMAC canonical
input is newline-joined:

```text
v2
UPPERCASE_HTTP_METHOD
/normalized/request/path
X-Telnexa-Timestamp
X-Telnexa-Event-Id
telnexa
X-Key-ID
SHA256(exact_raw_body)
```

The mounted shared signing key must be at least 32 bytes. Fixed private delivery
is relay -> `http://billing-api:8000/internal/v1/provider-events/jasmin`; redirects
and empty/unreadable signing identities fail closed. Root-owned provider source
keys still authenticate callbacks before the relay signs them. Signing binds
source-key identity as well as raw bytes, path, timestamp and event ID. Timestamp
window is 300 seconds. Exact authenticated replay is durably deduplicated without
reprocessing; altered replay is 409. Unauthenticated/stale/tampered callbacks are
401. JSON shape, duplicate keys, nesting and request sizes are bounded before
application processing. Credential query/body fields are stripped before relay.

Inbound ownership comes only from an explicit enabled `sms_inbound_bindings`
source-key/destination -> active PhoneNumber mapping. Migration creates no live
mappings. Unmapped events are quarantined, never assigned to the first tenant
with a matching destination or to a tenant supplied in the callback body.
Provider-local inbound IDs are namespaced by authenticated source. STOP is
persisted locally before outbox delivery, including during Middleware outage.
Callback webhook jobs reference their actual transactional outbox event.

## Health and validation

`GET /api/v1/integration/health` needs `sms.health.read`. It reports database and
receipt-schema readiness, source SHA and configured-provider count. It expressly
reports provider connectivity as `not_probed` and runtime certification as false;
configured providers are not proof of carrier connectivity.

The issue29 workflow validates source head and synthetic merge result separately.
It runs API/negative/replay/interruption tests and a fresh loopback PostgreSQL
migration/concurrency/immutable-receipt/quota test with all delivery flags false.
The certification script refuses any database except its dedicated disposable
localhost CI database. Artifacts contain only source SHA and pass/fail/counts,
not database dumps, test credentials or payloads. This is **source/laboratory
validation**, not evidence of a deployed cross-server image tuple.

## Required staging and production evidence (not supplied by source CI)

Keep issue #29 open until the deployment owner records the exact Telnexa API,
relay and Middleware source/image digests; verified signature/SBOM/provenance;
private endpoint/mTLS and approved secret-manager references; explicit inbound
bindings; approved synthetic destination, sender, route, limit and time window;
and independent review/activation authority. Keep SMS_DELIVERY and all live
channel flags false outside the separately authorized bounded canary.

Before promotion: migrate the disposable/staging database; prove callback v2,
read-only GET reconciliation, duplicate/altered/wrong-key/tenant/campaign cases,
STOP during downstream outage, dependency health, and zero unrelated effects.
A companion Middleware GET consumer must be deployed before relying on the new
read-back protocol; old POST-based reconciliation is not an accepted substitute.

Rollback rehearsals must pause new dispatch, preserve claims, inbox/outbox,
receipts, database backup and carrier evidence, and restore the previously
approved **API+relay matched tuple**. Additive receipt/binding tables remain;
do not delete receipts or reset uncertain jobs. Source rollback is not proof of
runtime rollback; old binaries must not blindly submit jobs written by the new
worker. Verify read-back and reconciliation before reopening traffic. No source
merge alone authorizes that cutover or a real SMS.
