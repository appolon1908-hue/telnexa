# Repository Profile — `telnexa`

## Identity

- **Repository:** `appolon1908-hue/telnexa`
- **Category:** Communications runtime — SMS
- **Visibility:** `private`
- **Default branch:** `main`
- **Authority:** Primary Telnexa SMS/Jasmin runtime and billing authority
- **Status:** Production-oriented SMS gateway and billing control plane; live carrier routing and production SMS remain gated.

## Purpose

Runs the governed SMS service around Jasmin HTTP/SMPP, Redis, RabbitMQ, signed MO/DLR callbacks, tenant quotas, wallets, immutable ledgers, usage, margins, invoicing foundations, and operator tooling.

## Owns

- Jasmin SMS submission, routing, inbound SMS, DLRs, and provider state
- SMS billing, wallets, reservations, usage, rates, and ledger evidence
- Provider-side health, callbacks, persistence, backup, and restore

## Does not own

- Public Telnexa marketing website
- Cross-system authorization, consent, or privileged application writes
- Voice/VICIdial operations

## Key integrations

- Middleware as the only privileged command boundary
- `SDK-repository` communications contracts
- Kong/Keycloak for governed access
- Carrier SMPP providers and signed callback delivery

## Current priorities

1. Complete Communications API v1 SMS mapping and reconciliation
2. Prove idempotency, Unicode, DLR/MO normalization, opt-out, and suppression behavior
3. Keep provider credentials and callback secrets outside Git
4. Certify backup/restore, billing correctness, immutable releases, and production gates

## Governance and safety

- Target promotion model: `feature/docs/fix/security/upgrade -> development -> test -> staging -> production -> main`.
- Use pull requests and exact-head/merge-result validation; merging source never authorizes deployment.
- Never commit carrier credentials, API tokens, private keys, customer messages, or billing secrets.
- Production images and releases must be immutable; mutable `latest` tags are not release authority.
- `LIVE_SMS_DELIVERY`, provider submission, and real carrier routes remain disabled until separate approval.
- This document does not send SMS, change routes, alter DNS/firewalls, or activate production.

## Account-wide catalog

See `appolon1908-hue/documentaions/REPOSITORY_CATALOG.md`.
