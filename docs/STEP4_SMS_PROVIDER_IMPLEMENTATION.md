# Step 4 — Telnexa SMS Provider Implementation

## Authority

- Repository: `appolon1908-hue/telnexa`
- Branch: `feat/communications-api-v1-sms-provider`
- SDK contract: `63c793e88cca5daecfb5c8a688b8674ab288c522`
- Middleware SMS authority: `d7fcb30dc70ed54bd7e444e4c2ab59f5b5b5d24e`

Middleware owns cross-system authorization, durable command intent, consent and suppression policy, and reconciliation coordination. Telnexa owns Jasmin translation, provider references, DLR/MO normalization, provider read-back evidence, and provider-side billing reservations.

## Additive private runtime

`billing/sms_provider_service.py` is a private provider service. It does not replace the public Telnexa commercial API. It accepts the canonical `sms.message.submit.v1` command and supports:

- tenant-scoped exact idempotency;
- one provider submission attempt;
- durable command, reference, event, callback, opt-out, billing, and reconciliation evidence;
- GSM-7 and UCS-2 segment validation;
- disabled-by-default transport;
- internal no-effect HTTP simulator transport;
- bounded authoritative read-back after unknown outcomes;
- signed and replay-safe DLR/MO callbacks;
- monotonic delivery status;
- STOP and HELP normalization;
- provider health, usage, opt-out, and callback read surfaces.

The provider journal stores content hashes and segment evidence, not outbound or inbound message bodies.

## API surface

```text
POST /api/v1/provider/operations
POST /api/v1/commands/sms.message.submit.v1
GET  /api/v1/provider/operations/{operation_id}
GET  /api/v1/messages/{operation_id}
GET  /api/v1/messages/by-middleware/{message_id}
POST /api/v1/provider/operations/{operation_id}/reconcile
POST /api/v1/provider/callbacks/dlr
POST /api/v1/provider/callbacks/mo
GET  /api/v1/provider/usage
GET  /api/v1/provider/opt-outs
GET  /api/v1/provider/callbacks
```

Provider command APIs require a dedicated Middleware bearer identity and matching `X-Tenant-ID`. Provider callbacks require timestamped HMAC-SHA256 signatures and unique event IDs.

## Persistence

`billing/migrations/004_sms_provider_runtime.sql` creates the durable PostgreSQL contract. It includes:

- one operation per tenant/idempotency key;
- one operation per tenant/Middleware message;
- one provider submission attempt enforced by both a check constraint and trigger;
- reconciliation and callback indexes;
- tenant row-level security policies;
- append-only provider event identity;
- durable billing reservation state;
- callback outbox and reconciliation evidence.

## Source certification

The Step 4 workflow validates the exact source head and exact GitHub merge result. It also checks out the frozen SDK and Middleware SHAs, runs the contract-lock validator, applies the PostgreSQL migration in a disposable database, starts a no-effect Jasmin simulator on an internal Docker network, and proves zero external effects.

CI run IDs and final exact Git identities belong in PR #20 review evidence. They are not predeclared in this document.

## Safety

```text
SMS_DELIVERY=false
LIVE_SMS_DELIVERY=false
JASMIN_LIVE_SUBMISSION=false
SMS_SENT=NO
PRODUCTION_DEPLOYED=NO
```
