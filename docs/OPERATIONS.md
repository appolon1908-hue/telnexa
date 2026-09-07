# Billing operations

Copy `.env.example`, run `scripts/generate-env.sh`, review domains, install the approved immutable image digests, keep simulator enabled, then run `docker compose config`, migrations, tests, `scripts/start.sh`, and `scripts/health.sh`. Do not build production artifacts on the server. Migrations require a fresh backup. Metrics are private through Prometheus; watch send status, duplicates, reservations, failed charges, outbox/DLQ, revenue/cost/margin, invoices and payments. Never label simulator outcomes as handset delivery.

Activation requires production SSH access, DNS for app/admin/API hosts, a SAN certificate, middleware identity/contract acceptance, and explicit provider credentials plus an authorized destination. Deploy only Telnexa services after backing up and recording the existing container/network inventory.
