# Production SMS adapter operations

`POST /api/v1/messages` durably accepts a commercial message before transport. It creates the existing Message, billing reservation, initial MessageEvent, Audit, and a new `sms_dispatch_jobs` row in one database transaction. The worker path is Message -> route decision -> dispatch attempt -> provider-neutral adapter -> private Jasmin. The simulator remains available only through `/api/v1/simulator/*`.

## Existing-component classification

| Classification | Components |
|---|---|
| REUSE | Message, MessageEvent, balance reservation, Wallet, immutable LedgerEntry, Usage, Rate, Provider, Route, Sender, Contact, ConsentRecord, CountryPolicy, InboundMessage, Audit, billing Outbox, Webhook/WebhookDelivery, OIDC and API-key authentication, tenant RLS pattern |
| EXTEND | Message dispatch/certainty timestamps and content required for deferred transport; Provider adapter/capability/limit metadata; commercial message response; metrics/alerts; webhook relay target; Compose/runtime |
| NEW | `sms_dispatch_jobs`, `sms_dispatch_attempts`, `sms_route_decisions`, `sms_provider_event_inbox`, `sms_provider_event_attempts`, `sms_reconciliation_cases`, `sms_production_canary_gates`; provider adapter interface; Jasmin HTTP adapter; dispatch, provider-event and reconciliation workers |
| DEPRECATE | Production `send_simulated(...)` use behind the commercial API; public `sms.telnexa.co/send`; Jasmin-to-Middleware callback bypass; Middleware-held Jasmin credentials |
| BLOCKED_EXTERNAL | Legitimate carrier/sandbox credentials, provider route/IP authorization, registered sender, authorized destination, independent approval, restricted production deployment/readback, and bounded real canary |

## Safety and activation

`TELNEXA_PRODUCTION_SMS_ENABLED=false` is hard-coded in the implementation Compose environment. The dispatch worker does not claim jobs while false. Enabling requires a reviewed release plus a durable, enabled, unexpired gate matching the exact tenant, sender, destination, and maximum submissions. Provider routing is separately disabled by default. Never place provider credentials in PostgreSQL: `Provider.credential_reference` is a file-prefix reference only, resolving the username, password, and DLR token under `/run/secrets`.

An adapter timeout after a request may have been written is `AMBIGUOUS`. The job and Message become `submission_unknown`, the billing reservation remains held, and an `ambiguous_submission` reconciliation case is created. No backup adapter is invoked. Only a proven pre-submit failure is eligible for bounded retry.

Jasmin callbacks flow through the relay to `/internal/v1/provider-events/jasmin`. The signature binds version, method, normalized path, timestamp, event ID, source, and exact body hash. The API persists before acknowledging. Tenant resolution is by provider-message mapping for DLR and assigned PhoneNumber for MO; provider tenant fields are ignored. STOP processing updates local suppression before asynchronous downstream notification.

## Migration, rollback, and restore

Run `python -m billing.migrate` with the migration role. Migration `004_production_sms_adapter.sql` is additive and idempotent and applies RLS to every tenant-owned adapter table. Validate with BASE -> NEW -> REAPPLY. Before deployment, run `scripts/backup.sh`, validate the custom dump with `pg_restore --list`, and restore into an isolated database using `scripts/restore.sh` or `pg_restore --exit-on-error`.

Rollback is application-first: keep production sending false, stop the three SMS workers, restore the previous immutable image, and leave additive tables/columns intact. Schema rollback is intentionally manual because dropping durable attempts, provider events, or reconciliation evidence destroys audit records. A later reviewed migration may archive and remove them only after retention requirements are satisfied.

## Middleware migration

Codestra Middleware must call `https://api.telnexa.co/api/v1/messages` with canonical client-credentials identity, `sms.send`, `Idempotency-Key`, correlation ID, and tenant context verified by Telnexa. Remove `sms.telnexa.co/send`, Jasmin username/password, and DLR business handlers from Middleware only after fake-provider E2E and an authorized sandbox pass. Telnexa outbox events remain the downstream contract. The Nginx `sms.telnexa.co` vhost now returns 410 and never proxies `/send` to Jasmin.

## External certification gate

No carrier credential, sender authorization, route authorization, or canary destination is committed or inferred. Until those are supplied and the restricted one-destination canary passes, the only valid terminal status is `TELNEXA_PRODUCTION_SMS_ADAPTER_SOFTWARE_READY_PROVIDER_GATED`.
