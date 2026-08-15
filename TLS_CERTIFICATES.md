# Telnexa TLS / Certificate Mission

Codex must treat certificate management as a production requirement for all enabled public Telnexa services.

## Public services

Use free Let's Encrypt certificates (ACME) for every public HTTPS/TLS hostname that is actually enabled, including as applicable:

- telnexa.co
- www.telnexa.co
- app.telnexa.co
- admin.telnexa.co
- api.telnexa.co
- sms.telnexa.co
- status.telnexa.co

## Requirements

- Obtain valid publicly trusted certificates only after DNS resolves correctly.
- Use Certbot, Traefik ACME, Caddy ACME, or another maintainable ACME client integrated with the chosen reverse proxy.
- Configure automatic renewal.
- Test renewal with the platform's dry-run/staging method where supported.
- Persist ACME account/certificate state across Docker restarts and server reboots.
- Reload/restart affected proxy/application services automatically after successful renewal where required.
- Force HTTPS for public web/API endpoints.
- Use secure TLS defaults and HSTS where appropriate.
- Monitor certificate expiry and surface renewal failures in logs/monitoring.
- Do not commit private keys, ACME account keys, or secrets to Git.
- Back up certificate configuration and document recovery/reissuance procedures.

## SMS-specific TLS

Where TLS is supported and required for SMPP/provider or customer connections, configure it according to the actual provider/client capability and document the port/certificate expectations. Do not invent carrier TLS settings.

Raw Jasmin management, Redis, RabbitMQ, PostgreSQL, Docker socket, and internal debug services must remain private and must not be exposed merely to obtain certificates.

## Validation

Codex must verify:

1. DNS resolution matches the intended server.
2. HTTPS presents the correct certificate for each hostname.
3. Certificate chain is valid.
4. Automatic renewal is enabled.
5. Renewal dry-run/test succeeds where possible.
6. Proxy/app reload occurs after renewal where needed.
7. Certificate state survives container/server restart.
8. Expiry monitoring is active.

SSH is separate from TLS. Continue using SSH public-key authentication for administration; do not replace SSH keys with Let's Encrypt certificates.
