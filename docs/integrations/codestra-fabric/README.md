# Telnexa SMS — Codestra Integration Fabric v2

## Authority

Telnexa owns SMS tenants/accounts, senders, templates, message acceptance, routing intent, billing/rating, Jasmin mappings, submission state, DLR/MO processing, STOP/HELP compliance, usage and provider health. Jasmin, RabbitMQ and Redis remain internal implementation components.

Middleware is the only cross-system write boundary. n8n may coordinate timing, approval and follow-up through Middleware but receives no Jasmin HTTP credential, SMPP credential, RabbitMQ access, Telnexa database access or provider secret.

## Communication path

```text
Odoo/product request -> Middleware -> Telnexa commercial API -> policy/billing -> durable dispatch -> Jasmin
Jasmin/provider DLR or MO -> Telnexa durable inbox -> Telnexa state/compliance -> outbox -> Middleware
n8n -> Middleware only -> Telnexa adapter
```

There is no Middleware-to-Jasmin fallback and no n8n-to-Jasmin path.

## Correctness

- Every accepted send stores request hash, tenant, sender, destination, content classification, rate snapshot, reservation, correlation and idempotency before provider submission.
- Exact replay returns the existing message/operation.
- Same key with a different payload returns a conflict.
- Provider timeout becomes `SUBMISSION_UNKNOWN`; reconcile before retry or failover.
- DLR/MO callbacks cannot choose tenant authority.
- STOP/HELP is handled locally and durably even when Middleware is unavailable.
- Middleware outage cannot cause provider resubmission.
- Telnexa outage cannot trigger raw Jasmin fallback.

## API surface

- sender and template lifecycle;
- transactional and campaign message submission;
- message and conversation status;
- inbound replies;
- suppression and opt-out state;
- usage, wallet/billing projection and provider health;
- signed Jasmin/provider callback inbox.

## Capabilities

```text
SMS_DELIVERY=false
SMS_CAMPAIGN_SEND=false
SMS_SENDER_WRITE=false
ODOO_WRITE=false
DEAD_LETTER_REPLAY=false
```

## Branch program

```text
feat/codestra-keycloak-webhooks
  -> integration/codestra-sms-fabric-v2
       -> integration/middleware-sms-api-v1
       -> automation/sms-event-outbox-v1
       -> feature/sms-sender-template-policy-v1
       -> feature/sms-optout-suppression-v1
       -> feature/sms-conversation-routing-v1
       -> test/sms-fabric-contracts-v1
```

No child branch activates external SMS or changes live Jasmin, SMPP, firewall, DNS, Keycloak, Kong or production runtime.