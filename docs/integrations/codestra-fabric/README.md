# Telnexa SMS — Codestra Integration Fabric v2

## Authority and implementation status

Telnexa owns SMS tenants/accounts, senders, templates, message acceptance,
routing intent, billing/rating, Jasmin mappings, submission state, DLR/MO,
STOP/HELP compliance, usage and provider health. Jasmin, RabbitMQ and Redis
remain internal implementation components.

This PR defines a **source-only design contract**, not new deployed `/v1/sms`
handlers. Current protected-source handlers and generated runtime OpenAPI under
`/api/v1` remain the implemented API authority. Cross-repository adapter mapping,
identity provisioning and isolated staging evidence remain required. Do not
restore outdated simulator-only assumptions over the accepted durable SMS code.

Middleware is the only cross-system write boundary. n8n coordinates timing,
approval and follow-up through Middleware, never through Jasmin/SMPP, RabbitMQ,
Telnexa database access or provider credentials.

## Communication path

```text
Odoo/product -> Middleware -> Telnexa commercial API -> policy/billing
             -> durable dispatch -> Jasmin
Jasmin/provider DLR or MO -> authenticated relay -> private Telnexa durable inbox
                         -> local state/compliance -> outbox -> Middleware
n8n -> Middleware only -> Telnexa commercial API adapter
```

There is no Middleware-to-Jasmin fallback, n8n-to-Jasmin path, or direct provider
callback bypass around Telnexa's durable state/compliance authority.

## Identity and callbacks

The Codestra machine trust is distinct from the local Telnexa portal trust.
Its resource identity/audience is `telnexa-gateway`; `telnexa-adapter` is an
internal Jasmin username, not a substitute resource audience. Machine callers
need an explicit allowlist, tenant/account binding, and short-lived signed tokens.
`sms.send` and `sms.status.read` are independent scopes. Local portal login and
its `telnexa-api` audience remain unchanged.

The provider callback contract is the private
`/internal/v1/provider-events/jasmin` inbox with source-key identity, raw-body
HMAC signature, canonical method/path/timestamp/event/source fields, bounded
freshness and durable deduplication. It is not an anonymous public webhook.
The OpenAPI apiKey-shaped signature scheme documents a calculated HMAC header,
not a reusable static API key. Runtime verification is still mandatory.

## Correctness

Every accepted send stores tenant, sender, destination, content classification,
request hash, rate snapshot, reservation, correlation and idempotency before
provider submission. Exact replay returns the existing operation; altered replay
returns conflict. `SUBMISSION_UNKNOWN` requires authoritative read-back and never
blind resubmission or failover. Provider callbacks cannot select tenant authority.
STOP/HELP remains local and durable during a Middleware outage. That outage
cannot cause a provider resend, and Telnexa outage cannot trigger raw Jasmin fallback.

## API design surface

Sender/template lifecycle, transactional and campaign message submission,
message/conversation state, inbound replies, suppression, usage, billing projection,
provider health, and authenticated provider callback acceptance. Proposed public
interfaces require tenant-bound machine scopes; writes also require idempotency
and correlation headers. Neither a design response nor a source merge proves a
live capability exists.

## Capabilities and validation

```text
SMS_DELIVERY=false
SMS_CAMPAIGN_SEND=false
SMS_SENDER_WRITE=false
ODOO_WRITE=false
DEAD_LETTER_REPLAY=false
```

Run `python -m scripts.validate_codestra_sms_fabric` and the full repository suite.
The validator rejects absent/malformed capability flags, anonymous callback
contracts, missing tenant binding, identity/scope drift and durable-path bypasses.
`tests/test_codestra_sms_fabric.py` adds negative regressions and OpenAPI schema
validation. The branch map is in `BRANCH_MAP.md`.

No source change enables external SMS, imports n8n workflows, or changes live
Jasmin, SMPP, firewall, DNS, Keycloak, Kong, email, voice or production runtime.
