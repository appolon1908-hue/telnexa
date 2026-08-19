# Billing architecture

The billing API is the authorization and charging boundary in front of message submission. PostgreSQL is authoritative for tenants, accounts, wallets, immutable ledger entries, reservations, rate snapshots, messages, usage, invoices, payments, audit, and the middleware outbox. The API, migration job, and event worker are separate containers. Jasmin, Redis, and RabbitMQ remain unchanged.

Prepaid flow is normalize and segment, resolve provider/sell rates, atomically lock wallet, reserve, submit to the simulator/Jasmin adapter, then finalize or release. Production provider submission is disabled until credentials and an authorized destination exist.
