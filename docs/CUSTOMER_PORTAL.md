# Customer portal

`app.telnexa.co/portal` is the customer shell. Tenant APIs expose messages, wallet, ledger, invoices, usage and OpenAPI documentation. The planned authenticated modules are API key rotation, SMPP credentials, sender requests, signed webhooks, statements, top-ups and team RBAC. The current build does not claim complete login/reset/MFA or interactive CRUD; production activation stays blocked until those flows and tenant-isolation tests are complete.
