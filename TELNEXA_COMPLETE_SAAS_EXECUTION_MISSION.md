# Telnexa Complete SaaS — One-Shot Autonomous Codex Execution Mission

## Mission

Take Telnexa from its current state to the strongest safely achievable production-ready multi-tenant SMS SaaS platform in one continuous execution.

This is a complete implementation mission, not a planning exercise. Inspect the existing repository and deployment first, preserve working production behavior, then build, migrate, Dockerize, deploy, test, harden, document, commit, push, and open/update the production PR.

Do not stop for routine engineering decisions. Diagnose ordinary failures, fix them, retest, and continue.

## Existing architecture to preserve

- Product: Telnexa
- Domain: telnexa.co
- Shared application server public IP: 37.27.128.39
- Shared application server private vSwitch IP: 10.40.0.2
- Middleware private IP: 10.40.0.1
- SMS transport: Jasmin SMS Gateway
- Upstream transport: SMPP providers when approved
- Middleware/n8n/Odoo remain separate systems
- Klyrow and Kyqra remain separate products, credentials, databases, Docker networks, and service identities even when sharing infrastructure

Never write directly to Odoo PostgreSQL. Telnexa communicates through the middleware control plane.

## Git workflow

Repository: appolon1908-hue/telnexa

Create/use branch:

`agent/complete-saas-platform`

Do not make substantial implementation changes directly on main.

Commit logically grouped changes, push the branch, and open/update a PR to main with architecture, migrations, security notes, tests, launch gates, and deployment evidence.

## 1. Production architecture

Build/complete a Docker-first architecture containing as appropriate:

- Telnexa API gateway/backend
- client portal
- admin portal
- Jasmin SMS Gateway
- Redis
- RabbitMQ
- PostgreSQL for Telnexa commercial/application state
- event/outbox workers
- webhook delivery workers
- billing workers
- routing/provider-health workers
- SMPP simulator/test SMSC
- Nginx or Traefik reverse proxy
- Prometheus
- Grafana
- exporters
- backup/restore tooling

Use isolated Docker networks. Do not expose Redis, RabbitMQ, PostgreSQL, Docker socket, Jasmin management interfaces, internal debug ports, or simulator internals publicly.

## 2. Tenant/customer model

Implement complete multi-tenancy.

Each tenant must support:

- tenant/customer ID
- organization/company
- status
- plan
- primary contact
- team members
- roles
- billing profile
- wallet
- credit limit where approved
- currency
- API keys
- SMPP credentials
- sender IDs
- phone numbers/long codes where applicable
- webhook endpoints/secrets
- allowed countries
- destination restrictions
- throughput limits
- daily/monthly quotas
- compliance/consent configuration
- suspension/termination state
- created/updated/audit metadata

Enforce tenant isolation server-side and in database queries.

## 3. Authentication and security

Client authentication must include:

- signup/onboarding foundation where enabled
- login
- logout
- forgot/reset password
- secure sessions
- secure cookies
- CSRF protection where applicable
- login rate limiting
- modern password hashing such as Argon2id
- email verification where appropriate
- optional/enableable TOTP MFA
- session/device management

Admin authentication must be separate and strongly protected.

Support RBAC roles such as:

- platform_superadmin
- platform_admin
- operations
- billing
- compliance
- support_readonly
- tenant_admin
- tenant_developer
- tenant_billing
- tenant_user
- tenant_readonly

All privileged actions must be audited.

## 4. Client portal — complete SaaS experience

Build a polished responsive client portal at `app.telnexa.co`.

Required sections:

### Dashboard
- balance/available credit
- today's/month's SMS volume
- spend
- delivery rate
- failure rate
- recent messages
- recent inbound conversations
- usage vs plan/quota
- sender status
- provider/service status appropriate for clients

### Messaging
- send single SMS
- authorized batch send where appropriate
- templates
- scheduled sends
- drafts
- message history
- advanced filters
- message detail timeline
- DLR status
- segment count
- cost
- client reference
- export CSV

Do not enable unsolicited bulk messaging. Require appropriate consent/authorization controls for marketing sends.

### Two-way inbox/conversations
- inbound SMS inbox
- conversation threads by contact/number
- reply from portal when sender/number supports it
- unread/read state
- assignment to team member where useful
- notes/tags
- contact link
- message timeline
- opt-out status

### Contacts/lists
- contacts
- lists/segments
- CSV import/export
- consent status
- consent source/time
- SMS opt-out state
- custom fields where useful
- deduplication

### Sender management
- sender IDs
- numbers
- registration/approval status
- country restrictions
- supporting documentation/status fields where required
- request new sender
- rejection reason

Do not falsely claim carrier approval.

### API/developer center
- API keys
- scoped keys
- rotation/revocation
- API logs
- request IDs
- OpenAPI docs
- code examples
- webhook management
- webhook secret rotation
- webhook delivery logs
- test webhook
- sandbox/simulator credentials

### SMPP center
- SMPP credentials
- host/port/TLS details when enabled
- bind mode
- IP allowlist
- throughput
- active bind count
- credential rotation
- connection status
- simulator/sandbox connection information

Never reveal stored secrets after initial creation unless securely regenerated.

### Billing
- wallet balance
- top-up history
- transaction ledger
- invoices
- statements
- rate card
- usage by destination
- spend by period
- downloadable invoices/statements
- payment method integration when configured
- low-balance alerts

### Account/team
- profile
- organization
- team users
- invitations
- roles
- MFA/security
- notification preferences
- logout

## 5. Admin portal — complete operations/business console

Build a separate secure admin portal at `admin.telnexa.co`.

### Executive dashboard
- total customers
- active/suspended customers
- SMS today/month
- revenue
- provider cost
- gross margin
- margin percentage
- outstanding balances
- prepaid liability
- queue depth
- delivery rate
- provider health
- alerts

### Customer management
- create customer
- approve/onboard
- edit
- suspend/reactivate
- terminate/soft-delete safely
- plans
- quotas
- TPS
- countries
- sender restrictions
- wallet/credit
- API/SMPP provisioning
- team/user management
- impersonation only if safely implemented with strong audit and visible banner

### Billing administration
- wallet credits/debits
- adjustments
- refunds
- credit limits
- invoices
- payment status
- statements
- taxes/configuration hooks
- plan pricing
- customer-specific pricing
- provider costs
- margins
- revenue reports
- aging/outstanding balances where applicable

Every money adjustment requires reason, actor, timestamp, and immutable audit entry.

### Provider/SMPP operations
- provider list
- connector state
- bind status
- health score
- submit success/failure
- DLR performance
- latency
- throttling
- TPS
- manual enable/disable/drain
- primary/backup assignment
- route testing with simulator

### Routing
- country routes
- MCC/MNC/network routes where data exists
- customer routes
- sender routes
- priority
- failover
- quality-based routing foundation
- least-cost routing foundation
- route version/history
- dry-run/preview before applying

### Sender/compliance operations
- sender approval queue
- country rules
- consent/opt-out status
- suppression list
- compliance flags
- abuse alerts
- complaint/support records where appropriate

### Message operations
- global message search with authorization
- message timeline
- DLR timeline
- provider response
- route used
- billing transaction
- webhook deliveries
- retry/replay controls where safe

### System operations
- containers/services
- Jasmin
- Redis
- RabbitMQ
- PostgreSQL
- workers
- queues
- event outbox
- DLQ
- webhook failures
- TLS expiry
- disk/CPU/RAM
- backups
- audit/security logs

## 6. Commercial billing engine

Build Telnexa's commercial billing truth outside raw Jasmin billing.

Use fixed-precision decimal or integer minor units. Never binary floating point for money.

Implement double-entry-style or otherwise immutable ledger accounting suitable for audit.

Support:

- wallet balance
- available balance
- reserved balance
- credit limit
- credits
- debits
- reservations
- settlement
- releases
- refunds
- adjustments
- expiration where business policy requires
- transaction references
- immutable history

A mutable balance may be cached, but ledger entries are the source of truth.

## 7. SMS pricing/rate cards

Support:

- provider cost
- customer sell rate
- country
- MCC/MNC/network when available
- sender type where relevant
- message type
- currency
- effective start/end dates
- plan-based rates
- customer-specific overrides
- markup rules
- minimum margin rules
- per-segment charging
- version history

Provide rate preview before activation.

Never silently overwrite historical rates used for prior messages.

## 8. Segment-aware billing

Correctly calculate GSM-7, GSM-7 extension characters, UCS-2/Unicode, concatenated multipart segments, and other supported encodings.

Before submission:

1. normalize destination
2. determine encoding
3. estimate segment count
4. resolve route/rate
5. estimate sell cost/provider cost
6. check wallet/credit/quota
7. reserve funds atomically
8. create message record
9. submit to transport
10. settle/release/refund according to configured billing policy and transport result

Idempotent retries must never charge twice.

## 9. Plans and subscriptions

Implement configurable plans such as:

- Starter
- Growth
- Business
- Enterprise
- Wholesale/Custom

Plans may control:

- monthly included usage if applicable
- API TPS
- SMPP TPS
- max binds
- team members
- sender IDs
- webhooks
- countries/features
- support tier
- markup/rate card
- reporting retention

Do not hard-code business prices in source code.

## 10. Invoicing and statements

Implement invoice/statement foundations with:

- unique sequential identifiers according to configurable jurisdiction/business rules
- customer billing details
- period
- line items
- SMS usage summary
- credits/refunds
- taxes hooks/configuration
- currency
- subtotal/total
- status
- payment references
- PDF generation
- CSV usage attachment/export

Integrate with middleware/Odoo for accounting/ERP according to the existing control-plane contract rather than writing directly to Odoo DB.

## 11. Payment abstraction

Create a provider abstraction so Stripe or another approved payment processor can be added without redesigning billing.

Support lifecycle concepts:

- top-up intent
- pending
- paid
- failed
- refunded
- chargeback/dispute hooks

Do not store raw card data.

Start with safe/manual/sandbox paths if payment credentials do not exist.

## 12. Profitability and finance analytics

Admin reporting must show:

- revenue by customer
- cost by provider
- margin by customer
- margin by destination
- margin by provider
- margin by route
- message count
- segments
- delivery rate
- refund/adjustment totals
- prepaid balances/liability

Allow CSV export.

## 13. Telnexa API

Keep Jasmin behind the commercial API.

Implement/complete versioned `/v1` APIs such as:

- POST /v1/messages
- POST /v1/messages/batch where safe
- GET /v1/messages
- GET /v1/messages/:id
- GET /v1/messages/:id/events
- GET /v1/balance
- GET /v1/transactions
- GET /v1/rates
- GET /v1/senders
- webhook CRUD/test
- API-key CRUD/rotation
- contact/list endpoints where product design requires
- inbox/conversation endpoints
- health/readiness endpoints

Requirements:

- scoped API keys
- hashed key storage where possible
- idempotency keys
- tenant isolation
- rate limits
- request IDs
- correlation IDs
- standardized errors
- OpenAPI documentation
- audit logging

## 14. SMPP reseller platform

Provision customers through Telnexa/Jasmin with:

- unique credentials
- bind transmitter/receiver/transceiver support where appropriate
- max binds
- TPS
- IP allowlists
- routing restrictions
- DLRs
- MO delivery
- multipart
- Unicode
- balance/quota enforcement
- account suspension
- credential rotation

Keep Jasmin management interfaces private.

## 15. Two-way SMS / inbound pipeline

Implement MO/inbound SMS end to end:

Provider/simulator → Jasmin → Telnexa → durable event store → middleware → n8n/Odoo/customer webhook/portal inbox.

Normalize inbound messages and associate with tenant/sender/number/contact where possible.

Support idempotency and duplicate MO protection.

## 16. Consent, STOP/HELP and compliance controls

Build configurable compliance controls appropriate to jurisdiction/use case.

Maintain:

- opt-in/consent status
- source
- timestamp
- evidence/reference where appropriate
- purpose/category
- opt-out status
- opt-out timestamp
- suppression reason

Implement configurable handling for standard opt-out/help keywords where applicable, including STOP-like and HELP-like behavior.

Do not automatically assume one country's rules apply globally. Build a country/policy rules framework and document that legal/compliance review is required for production jurisdictions.

Marketing sends must respect suppression/opt-out state.

Transactional/service messaging policy must remain separately configurable and lawful.

## 17. Campaigns/templates/scheduling

Provide safe campaign functionality for authorized opted-in messaging:

- templates
- personalization variables
- contact lists/segments
- scheduling
- timezone handling
- throttling
- quiet-hour policy support
- estimated recipients
- estimated segments/cost
- approval state for high-risk/large campaigns
- pause/cancel
- progress
- final analytics

Do not build tools intended to evade carrier or anti-spam controls.

## 18. Smart routing/provider marketplace foundation

Build provider management capable of multiple upstream SMPP providers.

Track:

- price
- supported countries/networks
- sender capabilities
- TPS
- health
- latency
- submit success
- DLR quality
- throttling
- outage state

Routing can consider:

- policy
- customer
- destination
- sender
- cost
- quality
- provider health
- capacity

Always honor configured compliance and commercial restrictions.

## 19. Provider health and failover

Implement health scoring and circuit breakers.

If a provider is unhealthy, route new eligible traffic to approved backup routes according to policy.

Prevent routing loops and duplicate billing/submission.

Provide manual drain/disable controls and recovery behavior.

## 20. Durable message/event lifecycle

Every message must have a Telnexa message ID and durable timeline.

Track:

- tenant
- client reference
- idempotency key
- sender
- destination
- encoding
- segments
- sell cost
- provider cost
- margin
- route
- connector
- Jasmin/provider IDs
- accepted/submitted/sent timestamps
- DLR events
- final state
- errors
- billing ledger references
- webhook state
- correlation ID

Normalize statuses such as accepted, queued, submitted, sent, delivered, failed, rejected, expired, undeliverable, unknown.

## 21. Middleware control-plane integration

Use private vSwitch communication with middleware at 10.40.0.1.

Keep a dedicated Telnexa service identity and secrets separate from Klyrow/Kyqra.

Use API authentication plus HMAC-SHA256 signed events, timestamps, event IDs, correlation IDs, replay protection, constant-time verification, idempotency, bounded retries, durable outbox, DLQ, and circuit breakers.

Support events such as:

- sms.accepted
- sms.queued
- sms.sent
- sms.delivered
- sms.failed
- sms.expired
- sms.received
- sms.opted_out
- sms.help_requested
- billing.reserved
- billing.settled
- billing.refunded
- wallet.low_balance
- provider.degraded
- provider.recovered
- campaign.started
- campaign.completed
- campaign.failed

Do not call arbitrary public n8n webhooks directly when middleware can orchestrate them.

## 22. Odoo/n8n integration

All Odoo operations go through middleware/approved Odoo APIs.

Support mappings for:

- customers/accounts
- contacts
- message activity/history
- inbound SMS
- delivery status
- opt-outs/preferences
- invoices/transactions where approved
- billing events
- support/activity triggers

n8n should orchestrate workflows such as inbound-message routing, delivery failures, low-balance notifications, campaign completion, account onboarding, and support alerts.

No direct Odoo database writes.

## 23. Webhooks

Customer webhooks must support:

- signed payloads
- timestamp
- event ID
- correlation ID
- retries with exponential backoff