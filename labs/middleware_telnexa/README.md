# Middleware ↔ Telnexa Isolated Certification Lab

This lab uses an ephemeral PostgreSQL database, a no-effect Jasmin HTTP simulator, the private Telnexa provider service, and a certification client. The network is Docker-internal and publishes no host ports.

Run locally where Docker Compose is available:

```bash
docker compose -f labs/middleware_telnexa/compose.yml up -d --build postgres jasmin-simulator provider
docker compose -f labs/middleware_telnexa/compose.yml run --rm certify
docker compose -f labs/middleware_telnexa/compose.yml down --volumes --remove-orphans
```

The simulator never opens an SMPP connection, never contacts a carrier, and always reports `sms_sent=0`.
