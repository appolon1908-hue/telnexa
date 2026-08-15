# Middleware billing events

Events include `billing.wallet.credited`, `billing.wallet.debited`, `billing.reservation.created`, `billing.reservation.released`, `billing.charge.finalized`, `billing.payment.received`, `billing.invoice.issued`, `billing.invoice.paid`, `billing.invoice.overdue`, `billing.low_balance`, and `billing.account.suspended` in envelope version `1.0`.

The outbox worker posts only to `BILLING_MIDDLEWARE_URL` on `10.40.0.1`, with bearer service identity and HMAC-SHA256 over `timestamp + newline + event_id + newline + telnexa + newline + body`. Headers carry event, timestamp, signature and idempotency. Middleware owns replay protection and Odoo/n8n routing. Telnexa never writes Odoo PostgreSQL or calls arbitrary n8n webhooks.
