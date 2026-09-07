# Repository Profile — `telnexa`

## Identity

- **Repository:** `appolon1908-hue/telnexa`
- **Category:** Communications runtime — SMS
- **Visibility:** `private`
- **Default branch:** `main`
- **Authority:** Primary Telnexa SMS/Jasmin runtime and billing authority
- **Status:** Production-oriented SMS gateway and billing control plane; live carrier routing and production SMS remain gated. Source readiness does not certify a live deployment.

## Purpose

Runs the governed SMS service around Jasmin HTTP/SMPP, Redis, RabbitMQ, authenticated MO/DLR callbacks, a durable provider-event inbox, tenant quotas, wallets, immutable ledgers, usage, margins, invoicing foundations, and operator tooling.

## Owns

- Jasmin SMS submission, routing, inbound SMS, DLRs, and provider state
- SMS billing, wallets, reservations, usage, rates, and ledger evidence
- Local STOP/HELP and suppression processing, including during Middleware outages
- Durable callback processing and signed transactional outbox delivery
- Provider-side health, persistence, backup, and restore

## Does not own

- Public Telnexa marketing website: `appolon1908-hue/Telnexa-web`
- Cross-system authorization, command orchestration, or privileged application writes: `appolon1908-hue/Middleware-`
- Voice/VICIdial operations: `appolon1908-hue/Vicidialer-Codestra`
- Email/Postal operations: `appolon1908-hue/klyrow.com`

## Key integrations

- Middleware as the only cross-system privileged command boundary, calling the Telnexa commercial API, never raw Jasmin
- `appolon1908-hue/SDK-repository` communications contracts
- Caddy/Kong/Keycloak for governed access; Redis/RabbitMQ remain private implementation components
- Local Telnexa portal identity remains separate from opt-in Codestra machine identity
- Carrier SMPP providers -> authenticated relay -> Telnexa durable inbox -> local processing -> outbox -> Middleware

## Current priorities

1. Validate current implemented APIs and generated OpenAPI against cross-repository SMS contracts; do not mistake historical planning gaps for absent code.
2. Retain regression evidence for idempotency, Unicode, DLR/MO normalization, local opt-out/suppression, billing, and authoritative read-back without blind resubmission.
3. Keep provider credentials, callback secrets, and machine-client allowlists outside Git.
4. Obtain fresh isolated staging evidence for backup/restore, signed immutable releases, identity, routing, and production gates before separately authorized activation.

## Governance and safety

- Follow the repository's actual protected-branch and environment rules. A catalog's proposed development/test/staging/production branch chain does not authorize creating, rewriting, or bypassing those branches.
- Use pull requests, exact source-head and current merge-result validation, resolved review threads, and fresh independent approval.
- Protected `main` is the immutable release source; staging and production activation require their own reviewed evidence and authorization. Merging source never authorizes deployment.
- Never commit carrier credentials, API tokens, private keys, customer messages, or billing secrets.
- Production images and releases must be immutable; mutable `latest` tags are not release authority.
- `LIVE_SMS_DELIVERY`, provider submission, and real carrier routes remain disabled until separate approval.
- Source changes do not send SMS, change routes, alter DNS/firewalls, activate machine trust, or deploy production.

## Account-wide catalog

See `appolon1908-hue/documentaions/REPOSITORY_CATALOG.md`. A catalog is descriptive; live repository protections and reviewed source contracts remain authoritative.
