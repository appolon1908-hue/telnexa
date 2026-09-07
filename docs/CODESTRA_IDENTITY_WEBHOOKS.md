# Codestra machine identity and durable SMS callbacks

## Source reconciliation — 2026-09-07

PR #15 is reconciled with protected main after #14, #22, #25, #27 and #28.
The previous branch must not replace the local portal issuer or restore a direct
Jasmin-to-Middleware relay. The current billing API, migrations, immutable release
workflow, portal, provider-event relay and workers remain authoritative.

## Separate identity trusts

The local portal continues to use `https://api.telnexa.co/auth/realms/telnexa`,
`telnexa-api`, and its sole private Keycloak JWKS endpoint. Its access-token
validator now requires the claims using PyJWT's supported `require` option.

The additive Codestra **machine-only** trust is disabled by default. It requires
`CODESTRA_OIDC_ENABLED=true` and a nonempty `CODESTRA_OIDC_ALLOWED_AZP` allowlist
provisioned outside Git. It accepts only the fixed Codestra issuer
`https://auth.codestra.co/realms/codestra`, exact audience `telnexa-gateway`, RSA
signatures using RS256, a recognized signing key, and mandatory `iss`, `sub`,
`aud`, `azp`, `iat`, `exp`, `jti`, `tenant_id` and `account_id` claims. The lifetime
must be positive and no more than 300 seconds. Tenant binding is mandatory.

The unverified issuer is only a routing hint to one of two fixed verifiers. It
cannot set a JWKS URL, bypass signature verification, or grant authorization.
Codestra JWKS retrieval uses a fixed HTTPS URL, rejects redirects, caps response
size, and bounds refreshes on unknown key IDs. Machine roles and wildcard scopes
never grant privileges. `sms.send` and `sms.status.read` remain distinct; a sender
cannot gain read-back scope implicitly. Existing API read aliases map only to
`sms.status.read`; unrelated writes still require their own explicit scopes.

`docker-compose.codestra-identity.yml` only exposes the disabled machine-trust
settings. It does not override local OIDC, publish ports, mount provider secrets,
redirect callbacks, or activate SMS. The ordinary full exact-head and merge-result
CI suite includes `tests/test_oidc_contract.py`; no parallel stale CI is needed.

## Durable callback authority

```text
Jasmin -> authenticated callback relay
       -> /internal/v1/provider-events/jasmin
       -> Telnexa durable provider-event inbox
       -> local state, billing and STOP/HELP processing
       -> durable outbox -> governed Middleware receiver
```

Provider callbacks cannot choose a tenant. Preserve source-key identification,
raw-body signatures, timestamp validation, durable deduplication, and exact replay
semantics in the accepted implementation. Do not restore the old branch's direct
`/api/v1/telnexa/events` dispatch in `docker/webhook-relay/server.py`; that skips
Telnexa's state/compliance authority. Codestra downstream token provisioning and
signed outbox delivery still require cross-repository staging evidence before
activation; this source change does not claim that runtime certification exists.

## Validation and activation boundary

Offline tests use generated test-only RSA keys and no network/provider traffic.
They cover both trusts, disabled defaults, signature failures, missing claims,
strict audience, allowlists, tenant/account binding, expiry/lifetime, scoped
read-back, role escalation, bounded key refresh and malformed tokens/JWKS.
Full CI, independent exact-head review, cross-repository contract validation and
isolated staging remain mandatory. Source merge never authorizes credential
installation, live migration, deployment, SMS/email/PSTN delivery or provider
provisioning. All existing live-effect flags remain unchanged and disabled.
