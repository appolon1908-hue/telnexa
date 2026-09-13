# Production SMS adapter operations

`POST /api/v1/messages` durably accepts a commercial message before transport. It creates the existing Message, billing reservation, initial MessageEvent, Audit, and a new `sms_dispatch_jobs` row in one database transaction. The worker path is Message -> route decision -> dispatch attempt -> provider-neutral adapter -> private Jasmin. The simulator remains available only through `/api/v1/simulator/*`.

## Existing-component classification

| Classification | Components |
|---|---|
| REUSE | Message, MessageEvent, balance reservation, Wallet, immutable LedgerEntry, Usage, Rate, Provider, Route, Sender, Contact, ConsentRecord, CountryPolicy, InboundMessage, Audit, billing Outbox, Webhook/WebhookDelivery, OIDC and API-key authentication, tenant RLS pattern |
| EXTEND | Message dispatch/certainty timestamps and content required for deferred transport; Provider adapter/capability/limit metadata; commercial message response; metrics/alerts; webhook relay target; Compose/runtime |
| NEW | `sms_dispatch_jobs`, `sms_dispatch_attempts`, `sms_route_decisions`, `sms_provider_event_inbox`, `sms_provider_event_attempts`, `sms_reconciliation_cases`, `sms_production_canary_gates`, immutable `sms_production_authorizations`/revocations, current `sms_delivery_policies`, and global `sms_system_controls`; provider adapter interface; Jasmin HTTP adapter; dispatch, provider-event and reconciliation workers |
| DEPRECATE | Production `send_simulated(...)` use behind the commercial API; public `sms.telnexa.co/send`; Jasmin-to-Middleware callback bypass; Middleware-held Jasmin credentials |
| BLOCKED_EXTERNAL | Legitimate carrier/sandbox credentials, provider route/IP authorization, registered sender, authorized destination, independent approval, restricted production deployment/readback, and bounded real canary |

## Safety and activation

`TELNEXA_PRODUCTION_SMS_ENABLED=false` is hard-coded in the implementation Compose environment. The dispatch worker does not claim jobs while false. Opening this deployment interlock is insufficient by design. Live acceptance and pre-submit dispatch also require an effective immutable authorization for the exact source SHA, an open global system control, and one current tenant policy in `TRANSACTIONAL_CANARY` or `TRANSACTIONAL_PRODUCTION`. `CAMPAIGN_PRODUCTION` is rejected. The policy binds the tenant and its unique billing account, DIDWW-tagged provider, approved sender, exact destination list, transactional categories, minute/hour/day segment ceilings, USD currency, and an initial total carrier-spend ceiling no greater than USD 2.00. USA routes are rejected. Canary mode additionally requires the existing bounded canary gate.

The privileged control surface is `/api/v1/admin/sms`. Mutations require a root-owned `production_operator_token` secret, operator identity, correlation ID, idempotency key, reason, and optimistic version where state exists. Authorization and revocation rows are database-immutable; policy/control changes append `Audit` and durable command-idempotency records. Repeating an identical mutation returns the original result, while conflicting reuse fails. Normal tenant administrators and messaging credentials cannot reach this control surface.

Acceptance selects one transport-eligible route by tenant specificity, longest destination prefix, priority, provider health, and stable ID. Sender authorization, country policy, capabilities, provider rate, sell-plan rate, network, margin, and route version are then validated against that same route and frozen into one immutable route decision. A denied policy or missing rate on the selected route cannot borrow authorization or pricing from a fallback route.

Never place provider credentials in PostgreSQL: `Provider.credential_reference` is a required file-prefix reference under `/run/secrets`. The Compose contract uses `/run/secrets/jasmin_http`, resolving exactly `jasmin_http_username`, `jasmin_http_password`, and `jasmin_http_dlr_token`. Worker startup and production readiness read all three files and report only boolean/readable status.

Each enabled provider also requires a unique `Provider.dlr_source_key_id` matching its webhook-relay source registry entry. The relay preserves that authenticated key in the durable inbox, and DLR correlation requires both this provider identity and the provider-local message ID. Missing or ambiguous matches are quarantined for reconciliation.

Provider capacity uses atomic PostgreSQL updates on shared `Provider.inflight_count`, `tps_window_started_at`, and `tps_window_count`; it does not use process-local counters. Capacity is released after the adapter call, while TPS consumption remains recorded for the one-second window.

An adapter timeout after a request may have been written is `AMBIGUOUS`. The job and Message become `submission_unknown`, the billing reservation remains held, and an `ambiguous_submission` reconciliation case is created. No backup adapter is invoked. Only a proven pre-submit failure is eligible for bounded retry.

Jasmin callbacks flow through the relay to `/internal/v1/provider-events/jasmin`. The signature binds version, method, normalized path, timestamp, event ID, source, and exact body hash. The API persists before acknowledging. Tenant resolution is by provider-message mapping for DLR and assigned PhoneNumber for MO; provider tenant fields are ignored. STOP processing updates local suppression before asynchronous downstream notification. When the selected inbound-number country policy explicitly allows inbound re-opt-in, a newer START/UNSTOP first clears local suppression and appends one `opt_in` ConsentRecord, then emits `sms.opted_in`; duplicate or out-of-order events cannot duplicate or reverse the newer consent effect.

## Migration, rollback, and restore

Run `python -m billing.migrate` with the migration role. Migrations `004_production_sms_adapter.sql` and `010_sms_production_policy.sql` are additive and idempotent. The latter preserves authorization and revocation evidence with database triggers and binds every accepted live job to its policy identity. Validate with BASE -> NEW -> REAPPLY. Before deployment, run `scripts/backup.sh`, validate the custom dump with `pg_restore --list`, and restore into an isolated database using `scripts/restore.sh` or `pg_restore --exit-on-error`.

Rollback is application-first: keep production sending false, stop the three SMS workers, restore the previous immutable image, and leave additive tables/columns intact. Schema rollback is intentionally manual because dropping durable attempts, provider events, or reconciliation evidence destroys audit records. A later reviewed migration may archive and remove them only after retention requirements are satisfied.

## Middleware migration

Codestra Middleware must call `https://api.telnexa.co/api/v1/messages` with canonical client-credentials identity, `sms.send`, `Idempotency-Key`, correlation ID, and tenant context verified by Telnexa. Remove `sms.telnexa.co/send`, Jasmin username/password, and DLR business handlers from Middleware only after fake-provider E2E and an authorized sandbox pass. Telnexa outbox events remain the downstream contract. The Nginx `sms.telnexa.co` vhost now returns 410 and never proxies `/send` to Jasmin.

## External certification gate

No carrier credential, sender authorization, route authorization, or canary destination is committed or inferred. Until those are supplied and the restricted one-destination canary passes, the only valid terminal status is `TELNEXA_PRODUCTION_SMS_ADAPTER_SOFTWARE_READY_PROVIDER_GATED`.

## Production incident runbooks

The authoritative normal-activation sequence is: certify the exact image/source SHA; reset the global system kill switch; create the immutable authorization; activate `TRANSACTIONAL_CANARY`; run and reconcile the sandbox/canary; then use a new authorization and explicit mutation to enter `TRANSACTIONAL_PRODUCTION`. Never edit policy tables directly.

- Emergency shutdown: set `TELNEXA_PRODUCTION_SMS_ENABLED=false` and redeploy the existing approved configuration. Also engage `POST /api/v1/admin/sms/system-kill-switch/engage`. Stop new dispatch only; keep the billing API, provider-event worker, Middleware outbox worker, metrics, database, and callback relay available.
- Provider failure or rejection spike: engage the system kill switch, open the provider circuit, retain queued/submission-unknown records, and reconcile by provider identity plus provider message ID. Do not route ambiguous attempts to a backup.
- DLR/MO backlog: keep outbound closed if callback observability is insufficient; keep relay and provider-event ingestion running; drain the durable inbox after authentication, duplicate, and out-of-order checks pass.
- Bounce-equivalent/undeliverable spike or complaint/STOP spike: stop expansion, engage the affected tenant or sender kill switch, preserve provider evidence, and confirm suppression before any reset.
- Sender or destination revocation: engage the sender or tenant kill switch, revoke the authorization, and issue a new owner-approved authorization rather than editing the immutable record.
- Quota adjustment: issue a new authorization carrying the reviewed limits, then activate it with the current expected policy version. Never modify quota columns directly.
- Credential rotation: keep outbound closed; rotate DIDWW/OpenBao, callback HMAC, mTLS, and Keycloak client material through their authorities; verify least privilege and callbacks; then reset the kill switch explicitly.
- Rollback: close the deployment interlock, preserve callback workers and evidence tables, restore the previous digest-pinned application/configuration, verify idempotency and reconciliation state, and do not drop additive tables.
- Backup restore: follow `docs/BACKUP_RESTORE.md` in an isolated environment first. Identify the exact encrypted archive and SHA-256 and record recovery time and critical SMS/audit reads.
- Re-enable after incident: require root cause, reconciled ambiguous submissions, healthy callbacks/metrics, current authorization, exact release SHA, owner sign-off, and a fresh bounded canary. Reset kill switches last.
