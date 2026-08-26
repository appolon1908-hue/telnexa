# Codestra Keycloak and middleware webhook integration

## Identity contract

Telnexa is a dedicated resource server and workload identity:

```text
issuer=https://auth.codestra.co/realms/codestra
resource_audience=telnexa-gateway
outbound_client_id=telnexa-gateway
machine_grant=client_credentials
maximum_token_lifetime_seconds=300
```

Inbound API tokens must have the exact `telnexa-gateway` audience, an approved
`azp`, a tenant and account binding, a `jti`, and the exact operation scope.
Middleware normally receives:

```text
sms.send
sms.status.read
```

The relay requests only `sms.events.publish` or `sms.inbound.publish` when it
publishes callbacks to the middleware API.

## Webhook contract

Jasmin callbacks are first authenticated against the root-owned provider-key
registry. Each key record must contain an authoritative `tenant_id`. The relay
then creates a stable event ID, builds the canonical Codestra event envelope,
obtains a short-lived Keycloak token, and posts to:

```text
${WEBHOOK_TARGET_BASE_URL}/api/v1/telnexa/events
```

The callback requires OIDC bearer authentication, mTLS in production, and an
HMAC-SHA256 signature over:

```text
v1
POST
/api/v1/telnexa/events
<unix-timestamp>
<event-id>
telnexa-gateway
<sha256-body>
```

Canonical headers include `Idempotency-Key`, tenant, event type, source,
timestamp, signature, and correlation ID. Stable event IDs make provider retries
idempotent at the middleware inbox.

## Runtime configuration

Use the reviewed Compose override:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.codestra-identity.yml \
  config
```

The following values remain outside Git:

```text
TELNEXA_GATEWAY_CLIENT_SECRET_FILE
TELNEXA_MIDDLEWARE_WEBHOOK_HMAC_FILE
TELNEXA_MIDDLEWARE_CA_FILE
TELNEXA_MIDDLEWARE_CLIENT_CERT_FILE
TELNEXA_MIDDLEWARE_CLIENT_KEY_FILE
TELNEXA_PROVIDER_KEYS_FILE
```

## Required CI evidence

Both the exact source SHA and the GitHub merge-result SHA must pass the identity
contract workflow. The full repository workflow must also pass formatting,
Ruff, the complete test suite, dependency audit, Compose rendering, non-root
image validation, and Gitleaks. Checkout credentials are not persisted; the
secret scanner receives only read access to repository contents and pull-request
metadata.

Do not activate the override until `telnexa-gateway`, its scopes and audience,
the middleware receiver, certificates, replay store, and rollback path have all
passed staging validation.
