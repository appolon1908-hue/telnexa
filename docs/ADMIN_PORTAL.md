# Admin portal

`admin.telnexa.co/admin` is the operations shell. Admin credit mutations require a secret token, reason, idempotency key and audit row. Customer lifecycle, rate imports, invoice controls, refunds, provider routing, security review and RBAC remain fail-closed until implemented. No UI action directly mutates Jasmin, Odoo, or ledger history.
