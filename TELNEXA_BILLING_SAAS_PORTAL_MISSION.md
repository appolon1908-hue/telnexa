# Telnexa Billing & SaaS Portal — Full Autonomous Codex Mission

You have full autonomy inside the `appolon1908-hue/telnexa` repository and on the designated Telnexa deployment host to build, install, configure, migrate, test, harden, document, and deploy a complete commercial SaaS billing and customer-portal layer around the existing Telnexa/Jasmin platform.

Proceed continuously until the definition of done is satisfied. Do not stop for routine implementation choices. Diagnose ordinary failures, fix them, retest, and continue.

Do not destroy unrelated production data. Back up configuration and databases before destructive migrations. Do not commit secrets. Do not invent carrier credentials. Do not send real SMS until valid provider credentials and an explicitly authorized test destination are supplied.

## Environment

- Repository: `appolon1908-hue/telnexa`
- Domain: `telnexa.co`
- Shared application server public IP: `37.27.128.39`
- Shared application server private vSwitch IP: `10.40.0.2`
- Middleware private vSwitch IP: `10.40.0.1`
- Existing SMS core: Jasmin SMS Gateway
- Existing stack may include Redis, RabbitMQ, PostgreSQL, API gateway, dashboards, monitoring, simulator, reverse proxy, TLS, and middleware integration

Inspect the existing branch/repository/deployment before making changes. Preserve working production behavior.

## Primary objective

Transform Telnexa into a complete commercial multi-tenant SMS SaaS platform with:

- prepaid and postpaid-capable billing foundations
- customer wallets and immutable ledger
- provider cost accounting
- customer sell pricing
- rate cards and pricing plans
- usage metering
- balance reservation and finalization
- invoice engine
- statements
- top-up/payment abstraction
- customer portal
- admin/billing portal
- API/SMPP provisioning
- sender management
- quotas and throughput controls
- margin/profitability reporting
- audit logs
- tenant isolation
- simulator-safe end-to-end testing
- Dockerized deployment
- monitoring
- backups
- middleware/Odoo/n8n integration

The system must be production-oriented, not a demo.

# 1. Billing data model

Build a normalized billing model in PostgreSQL or the existing Telnexa application database.

Required entities include:

- tenants/customers
- billing accounts
- wallets
- currencies
- ledger entries
- balance reservations
- provider rate cards
- customer rate cards
- pricing plans
- pricing overrides
- invoices
- invoice line items
- statements
- payments
- refunds
- credits/debits
- tax metadata where applicable
- usage records
- SMS message charges
- adjustments
- disputes/notes where useful

Use UUIDs where appropriate.

Never use binary floating point for money. Use fixed-precision decimal or integer minor units.

Ledger entries must be append-only/immutable except for explicit reversal entries.

Every monetary change must include:

- ledger_entry_id
- tenant_id
- billing_account_id
- currency
- amount
- direction/debit-credit semantics
- type
- reference_type
- reference_id
- idempotency_key
- actor/service
- timestamp
- metadata

Do not allow silent balance edits.

# 2. Wallet and balance engine

Implement:

- available balance
- reserved balance
- pending balance where needed
- credit limit
- low-balance threshold
- minimum balance rule
- negative balance policy
- account freeze/suspension policy

For prepaid sending, implement this sequence:

1. receive authorized send request
2. normalize recipient/message
3. estimate segment count
4. resolve sell rate
5. calculate estimated charge
6. validate balance/credit
7. reserve funds atomically
8. submit to SMS path
9. finalize charge or release reservation according to configured billing policy
10. record immutable ledger event

Handle failures safely.

If submission fails before an agreed billable state, release the reservation.

Do not double charge retries.

# 3. Idempotent charging

Every API send must support an idempotency key.

The same tenant + idempotency key must not:

- create multiple messages
- reserve funds twice
- charge twice
- issue duplicate refunds

Implement database-level uniqueness/transaction controls where practical.

Add tests for concurrent duplicate requests.

# 4. Segment and encoding billing

Calculate SMS segmentation correctly for:

- GSM-7
- GSM-7 extension characters
- UCS-2/Unicode
- multipart SMS

Store:

- encoding
- character count
- segment count
- estimated charge
- actual charge

Pricing must support per-segment billing.

Do not estimate one SMS when the actual message contains multiple segments.

# 5. Provider cost rates

Create provider cost rate cards.

Support:

- provider
- connector
- country
- MCC/MNC/network where available
- destination prefix
- message type/sender type where needed
- currency
- cost per segment
- minimum charge where required
- effective_from
- effective_to
- priority/specificity

Keep historical rates so old messages can be audited using the rate active at send time.

# 6. Customer sell rates

Support:

- global default rates
- plan-based rates
- country rates
- network/MCC-MNC rates
- customer-specific overrides
- volume tiers
- percentage markup
- fixed markup
- minimum margin rules
- effective dates

Rate resolution must be deterministic and auditable.

Every charged message should store the resolved provider cost rate and customer sell rate snapshot used at that time.

# 7. Pricing plans

Implement configurable plans such as:

- Starter
- Growth
- Business
- Enterprise
- Wholesale
- Custom

Plans can define:

- default pricing markup
- monthly included volume if enabled
- HTTP API TPS
- SMPP TPS
- daily/monthly SMS quotas
- max API keys
- max SMPP binds
- sender IDs allowed
- team users
- webhook limits
- countries allowed
- support tier metadata
- billing type prepaid/postpaid

Do not hard-code these values; store them as configurable plan data.

# 8. Usage metering

Meter usage by:

- tenant
- API key
- SMPP account
- user
- country
- network
- provider
- sender ID
- hour/day/month
- segment count
- status

Expose usage summaries for customer and admin dashboards.

Usage records must be idempotent and traceable to message IDs.

# 9. Profitability and margin analytics

Admin analytics must show:

- provider cost
- customer revenue
- gross profit
- gross margin percent
- message count
- segment count
- delivered count
- failed count
- cost by country
- revenue by country
- revenue by tenant
- margin by tenant
- provider profitability
- provider quality vs cost

Use rate snapshots rather than recalculating historical records from current rates.

Add date filtering and CSV export.

# 10. Invoice engine

Build a real invoice subsystem.

Support:

- sequential invoice numbering
- tenant billing profile
- billing period
- issue date
- due date
- currency
- subtotal
- taxes where configured
- credits
- total
- amount paid
- amount due
- status
- line items
- usage summary
- payment references
- notes

Statuses should include at least:

- draft
- issued
- paid
- partially_paid
- overdue
- void
- credited

Generate downloadable PDF invoices and machine-readable JSON/CSV where useful.

Do not expose another tenant's invoices.

# 11. Statements and transaction history

Customer portal must provide:

- transaction ledger
- opening balance
- credits
- debits
- reservations
- released reservations
- refunds
- adjustments
- payments
- closing balance

Allow filtering by date/type/reference.

Create downloadable monthly statements.

# 12. Payment/top-up abstraction

Create a payment-provider abstraction.

Do not lock the application to one provider.

Support future adapters for:

- Stripe
- PayPal or alternatives
- bank transfer/manual payment
- internal/Odoo payment confirmation

For now, implement safe mock/manual provider and a clean interface for production payment providers.

Support:

- top-up initiation
- pending payment
- successful payment
- failed payment
- refunded payment
- webhook verification
- idempotent payment processing

Never trust browser-return status alone. Payment confirmation must come from authenticated provider/server-side verification.

# 13. Postpaid/credit accounts

Support approved postpaid accounts with:

- credit limit
- current exposure
- billing period
- invoice due terms
- account suspension threshold

Default ordinary new tenants to prepaid unless configured otherwise.

Admin must explicitly approve postpaid/credit status.

# 14. Customer portal

Build/complete a polished responsive customer portal under an appropriate Telnexa hostname such as `app.telnexa.co` or `portal.telnexa.co`.

Required authentication:

- login
- logout
- forgot password
- password reset
- secure sessions
- CSRF protection where applicable
- rate-limited login attempts
- modern password hashing
- optional TOTP MFA foundation

Required customer pages:

## Dashboard

Show:

- current balance
- reserved balance
- credit limit if applicable
- spend today
- spend this month
- SMS today/month
- segments today/month
- delivery rate
- recent messages
- recent charges
- low-balance warning
- account status

## Messages

Provide:

- message history
- search
- filters
- destination
- sender
- status
- provider where authorized
- segments
- cost
- submitted timestamp
- DLR timeline
- correlation/request ID

## API keys

Allow:

- create key
- display secret once
- revoke
- rotate
- scopes
- rate limit
- last-used metadata
- optional IP allowlist

Store secrets hashed where practical.

## Developer area

Provide:

- REST API endpoint
- OpenAPI documentation
- code samples
- authentication instructions
- idempotency instructions
- webhook instructions
- example errors
- request log/error log where appropriate
- simulator/sandbox documentation

## SMPP

Show authorized customer SMPP details:

- host
- port
- system ID
- generated password displayed once/reset workflow
- bind type
- TPS
- max binds
- IP allowlist
- connection status

## Sender IDs

Allow:

- submit sender ID request
- see approval status
- supported countries
- documentation notes

Admin approval must be required where policy demands it.

## Webhooks

Allow:

- endpoint configuration
- event selection
- signing secret rotation
- recent deliveries
- failures
- manual resend where authorized
- test webhook

## Billing

Show:

- wallet balance
- transaction history
- rate card
- invoices
- statements
- top-up
- payment status
- usage/quota

## Team

Support organization users with roles such as:

- owner
- admin
- developer
- billing
- analyst/read-only

Enforce tenant isolation server-side.

# 15. Admin portal

Build/complete a secure admin portal under `admin.telnexa.co`.

Roles should include:

- superadmin
- operations
- billing
- support
- finance/read-only

Admin features:

## Customer management

- create tenant
- approve
- suspend
- reactivate
- close/soft-delete
- change plan
- prepaid/postpaid mode
- credit limit
- quotas
- TPS
- country restrictions
- sender restrictions

## Billing

- wallet balance
- immutable ledger
- manual credit/debit via adjustment entries
- refund
- reservation view
- invoices
- payments
- statements
- overdue accounts
- low-balance accounts

Every adjustment must require:

- reason
- actor
- timestamp
- amount
- reference
- audit event

## Pricing

Admin must manage:

- provider cost rates
- customer sell rates
- plans
- custom overrides
- effective dates
- margins

Include import/export CSV tooling with validation and dry-run preview.

## Provider/routing controls

Show:

- connector state
- provider health
- cost rates
- quality metrics
- route priority
- failover
- manual drain/disable

## Margin dashboard

Show profitability by:

- date
- tenant
- country
- network
- provider
- route

## Messaging

- message search
- message timeline
- DLR timeline
- cost/revenue/margin
- webhook deliveries
- audit/correlation IDs

## Security and abuse

- failed logins
- API abuse
- unusual volume spikes
- sender violations
- suspended tenants
- key revocation
- audit logs

Every privileged admin action must create an audit record.

# 16. Tenant isolation

Add explicit automated tests proving tenant A cannot access tenant B's:

- balance
- ledger
- invoices
- messages
- API keys
- SMPP credentials
- sender IDs
- rate overrides
- payments
- statements
- webhooks
- team members

This must be enforced in backend authorization, not just hidden in the UI.

# 17. Middleware/Odoo/n8n integration

Telnexa must continue integrating through middleware at `10.40.0.1`.

Do not write directly to Odoo PostgreSQL.

Create versioned billing events such as:

- billing.wallet.credited
- billing.wallet.debited
- billing.reservation.created
- billing.reservation.released
- billing.charge.finalized
- billing.payment.received
- billing.invoice.issued
- billing.invoice.paid
- billing.invoice.overdue
- billing.low_balance
- billing.account.suspended

Use:

- service identity/API key
- HMAC-signed events
- timestamp
- event ID
- correlation ID
- idempotency
- replay protection

Middleware may route events to Odoo/n8n.

Odoo should remain the accounting/ERP authority where existing architecture requires it.

Build adapters, not direct DB writes.

# 18. Message lifecycle billing integration

Connect billing to the existing Telnexa message lifecycle.

Each message should retain:

- tenant_id
- message_id
- provider/Jasmin ID
- client reference
- idempotency key
- encoding
- segments
- provider rate snapshot
- customer rate snapshot
- estimated provider cost
- estimated sell amount
- actual provider cost if known
- actual sell amount
- reservation ID
- ledger entry IDs
- refund/reversal IDs where applicable
- status
- DLR timestamps

# 19. Simulator billing tests

While real carrier approval is pending, use the SMS simulator to test:

- successful message billing
- failed submission releasing reservation
- insufficient balance
- multipart billing
- Unicode billing
- duplicate idempotency key
- provider reject
- delayed DLR
- failed DLR
- duplicate DLR
- failover route with different provider cost
- refund/reversal policy
- postpaid credit limit

Never claim simulator messages were delivered to a real handset.

# 20. Tax and regulatory extensibility

Do not hard-code one country's tax rules.

Create extensible fields for:

- business legal name
- billing address
- tax ID/VAT/GST number
- tax jurisdiction
- tax rate
- tax-exempt status

Keep production tax calculations configurable and documented.

Do not claim legal/tax compliance without actual jurisdiction-specific configuration.

# 21. Notifications

Create notification events/templates for:

- low balance
- payment received
- payment failed
- invoice issued
- invoice overdue
- credit limit warning
- account suspended
- API key rotated
- SMPP credentials changed

Use middleware/Klyrow email and/or Telnexa SMS only where configured and consented.

Avoid circular dependencies that block critical billing operations.

# 22. Observability

Add Prometheus metrics and Grafana dashboards for:

- wallet balances aggregate
- reservations
- charges/minute
- revenue/day
- provider cost/day
- gross margin/day
- failed charges
- duplicate/idempotent requests
- insufficient balance rejects
- invoice counts/status
- payment success/failure
- overdue accounts
- top-up volume

Never expose individual secrets or sensitive financial details in metrics.

# 23. Audit logs

Audit at minimum:

- plan changes
- rate changes
- credits/debits
- refunds
- credit-limit changes
- suspension/reactivation
- invoice changes
- payment adjustments
- API key changes
- SMPP credential resets
- sender approvals

Include actor, tenant, action, target, timestamp, request/correlation ID, and before/after summaries where safe.

# 24. Security

Implement/test:

- HTTPS with automatic Let's Encrypt renewal for public web/API services
- secure cookies
- HSTS where appropriate
- CSP where appropriate
- CSRF protection
- SQL injection protection
- XSS protection
- API rate limiting
- login rate limiting
- password reset expiry
- MFA foundation
- secret rotation
- SSRF protection on webhooks
- least-privilege Docker networks
- no public Redis/PostgreSQL/RabbitMQ/Jasmin management
- no Docker socket exposure
- log redaction

Do not weaken security to make tests pass.

# 25. Docker and deployment

Keep all practical components Dockerized.

Add/extend Compose services for:

- billing API/service if separated
- billing worker
- invoice worker
- notification/event worker
- customer portal
- admin portal
- database migrations

Use:

- health checks
- restart policies
- persistent volumes
- resource limits where practical
- isolated networks
- controlled image versions

Do not disturb Kyqra or Klyrow services on the shared host.

# 26. Backups and disaster recovery

Back up:

- billing database
- ledger
- rates
- invoices
- payment records
- application config
- reverse proxy config

Create/test restore tooling in a safe environment.

The ledger must be recoverable and auditable.

# 27. Migration safety

Before schema migrations:

- create database backup
- validate migration plan
- use transactional migrations where possible
- provide rollback procedure where feasible

Do not delete historical billing data.

# 28. Required automated tests

Create comprehensive tests covering at least:

## Billing

- wallet credit
- wallet debit
- reservation create
- reservation release
- reservation finalize
- insufficient balance
- concurrent sends
- duplicate idempotency key
- no double charge
- refund/reversal
- immutable ledger behavior

## Pricing

- country rate
- network-specific rate
- tenant override
- plan rate
- effective dates
- multipart price
- Unicode price
- margin calculation

## Plans/quotas

- TPS restriction
- monthly quota
- sender restriction
- country restriction
- plan change

## Invoices

- draft
- issue
- line items
- payment allocation
- overdue
- PDF generation
- tenant isolation

## Payments

- verified success
- invalid webhook
- duplicate webhook
- failed payment
- refund

## Portal

- customer login/logout
- password reset
- RBAC
- API key create/revoke
- invoice access
- statement export
- sender request
- SMPP access
- billing access

## Admin

- admin login/logout
- role restrictions
- credit adjustment audit
- rate import validation
- suspend/reactivate
- plan change
- invoice management

## Security

- tenant A cannot access tenant B
- invalid API key
- revoked API key
- replayed webhook
- invalid HMAC
- rate limiting

## Infrastructure

- container restart
- server reboot persistence where possible
- backup
- restore
- private databases
- TLS renewal configuration

# 29. Git workflow

Do not implement major changes directly on main.

Create/use branch:

`agent/billing-saas-portal`

Commit logically grouped changes.

Push the branch.

Open a pull request to main or the current production base branch if repository workflow requires another base.

Update existing Telnexa production PR instead only if that is clearly the intended branch strategy and does not mix unrelated unstable work.

# 30. Documentation

Create/update:

- `README.md`
- `DEPLOYMENT_REPORT.md`
- `docs/BILLING_ARCHITECTURE.md`
- `docs/WALLET_LEDGER.md`
- `docs/RATE_CARDS.md`
- `docs/PRICING_PLANS.md`
- `docs/INVOICES.md`
- `docs/PAYMENTS.md`
- `docs/CUSTOMER_PORTAL.md`
- `docs/ADMIN_PORTAL.md`
- `docs/SMPP_PROVISIONING.md`
- `docs/MIDDLEWARE_BILLING_EVENTS.md`
- `docs/SECURITY.md`
- `docs/BACKUP_RESTORE.md`
- `docs/OPERATIONS.md`

Document exact configuration and testing status.

# 31. Definition of done

Mission is complete only when:

- wallet system works
- immutable ledger works
- balance reservation works
- no-double-charge/idempotency tests pass
- provider cost rates work
- customer sell rates work
- plans work
- usage metering works
- margin reporting works
- invoices work
- statements work
- payment abstraction works with controlled/mock/manual provider
- postpaid credit controls work
- customer portal works
- admin portal works
- API key management works
- SMPP provisioning works
- sender request/approval flow works
- webhooks work
- middleware/Odoo/n8n event integration is documented and tested where connectivity allows
- tenant isolation passes
- audit logs work
- simulator billing tests pass
- Docker services are healthy
- restart persistence passes
- backups succeed
- restore procedure is validated where practical
- TLS auto-renew configuration exists for public services
- documentation is complete
- branch is pushed
- PR is opened
- deployment report clearly states remaining external blockers

# 32. Working rules / autonomy

You have full autonomy for normal engineering work required by this mission.

You may:

- create and edit application code
- create database migrations
- install required packages
- create Docker services
- modify Telnexa-specific Compose/configuration
- create scripts
- run tests
- restart Telnexa services
- create backups
- configure safe firewall rules specific to required Telnexa services
- configure reverse-proxy routes after backing up shared configuration
- create Git branches/commits/PRs

Do not:

- wipe disks
- delete unrelated services
- overwrite unrelated Kyqra/Klyrow data
- commit production secrets
- invent payment credentials
- invent SMPP credentials
- send real SMS without explicit authorization
- disable the firewall globally
- weaken authentication
- modify Odoo database directly
- merge to protected production branches unless repository policy and required tests allow it

Continue until completion.

Only stop when blocked by:

- a missing external credential or provider account
- a provider-level DNS/rDNS/payment action outside the server
- an operation that would destroy unrelated production data
- an ambiguous existing production integration that cannot be safely inspected or inferred

When blocked, complete every independent task first, then report the precise blocker and exact manual action required.
