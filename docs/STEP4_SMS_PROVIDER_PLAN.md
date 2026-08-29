# Step 4 — Communications API v1 Telnexa SMS Provider

## Authority

Repository: `appolon1908-hue/telnexa`

Branch: `feat/communications-api-v1-sms-provider`

Frozen SDK contract: `appolon1908-hue/SDK-repository@63c793e88cca5daecfb5c8a688b8674ab288c522`

Telnexa/Jasmin owns SMS provider behavior. Middleware owns cross-system authorization, durable command state, policy and reconciliation coordination.

## Scope

Prepare the provider-side mapping required by Communications API v1:

- canonical SMS request translation to Jasmin HTTP/SMPP;
- sender identity and route eligibility checks;
- GSM/Unicode coding behavior;
- provider message/reference retention;
- DLR normalization;
- inbound/MO normalization;
- provider failure classification;
- signed callback generation;
- replay-safe event IDs;
- provider/carrier health evidence;
- billing/wallet/usage read evidence where applicable;
- authoritative read-back/reconciliation support for uncertain submissions;
- provider-specific status preservation alongside canonical mapping.

## Callback requirements

Keep the existing Telnexa signed callback boundary. Middleware must be able to verify signature, timestamp, source/event ID and replay window. Never expose webhook secrets in logs or payloads.

## Consent and opt-out

Middleware is the policy enforcement authority before outbound submission. Telnexa must also preserve provider-originated inbound STOP/opt-out and carrier/provider evidence so canonical consent/suppression state can be updated safely.

## Unknown outcome rule

A transport timeout is not proof of failure. When a submit outcome is uncertain, expose enough provider/reference/read-back evidence to let Middleware reconcile before any retry.

## Required tests

- canonical request mapping;
- idempotent provider behavior where supported;
- GSM/Unicode encoding;
- sender/route validation;
- DLR creation and normalization;
- inbound/MO normalization;
- signed webhook generation;
- failure/expiry mapping;
- provider reference retention;
- read-back/reconciliation evidence;
- provider health;
- billing/usage read mapping where exposed;
- no live carrier delivery when production gates are disabled.

## Safety

Do not add or activate real carrier credentials/routes, send external SMS, change production DNS/TLS, deploy to production, or alter live billing balances as part of this branch.

## Exit gate

Step 4 provider work passes only when the paired Middleware SMS branch conforms to the frozen SDK contract, callback security and replay controls pass, uncertain outcomes reconcile without duplicate sends, and exact CI/source evidence is recorded.