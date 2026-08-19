# Billing backup and restore

`scripts/backup.sh` takes a PostgreSQL custom-format dump alongside existing config/Jasmin/Redis backups. Encrypt and copy it off-host. Before schema change, run the backup and verify `pg_restore --list`. Restore requires `CONFIRM_RESTORE=YES`, recreates the billing database, uses `pg_restore --exit-on-error`, restarts, and runs health checks. Validate ledger totals, wallet reconciliation, rate counts, invoices, payments, outbox, and simulator sends on an isolated clone before production recovery.
