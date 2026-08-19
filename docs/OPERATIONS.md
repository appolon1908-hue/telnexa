# Billing operations

Copy `.env.example`, run `scripts/generate-env.sh`, review domains, keep simulator enabled, then run `docker compose config`, `docker compose build billing-api`, migrations, tests, and `scripts/health.sh`. Migrations require a fresh backup. Metrics are private through Prometheus; watch send status, duplicates, reservations, failed charges, outbox/DLQ, revenue/cost/margin, invoices and payments. Never label simulator outcomes as handset delivery.

Activation requires production SSH access, DNS for app/admin/API hosts, a SAN certificate, middleware identity/contract acceptance, and explicit provider credentials plus an authorized destination. Deploy only Telnexa services after backing up and recording the existing container/network inventory.
