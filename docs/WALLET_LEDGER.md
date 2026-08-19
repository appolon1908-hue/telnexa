# Wallet and ledger

Money uses `NUMERIC(18,6)` and Python `Decimal`. Wallet available/reserved/pending values are cached transactionally; the append-only ledger is the audit authority. PostgreSQL rejects UPDATE/DELETE on ledger rows. Corrections create explicit reversal entries. Tenant plus idempotency keys are unique for both reservations and ledger entries. Prepaid rejects unavailable funds; postpaid adds only an explicitly approved credit limit.
