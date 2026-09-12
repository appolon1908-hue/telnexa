# Telnexa monitoring → Middleware → Odoo API contract

This repository now exposes the Telnexa side of the observability integration. It
is deliberately narrow:

- Telnexa exposes authenticated, read-only service metadata at
  `GET /api/v1/integration/observability` and
  `GET /api/v2/integration/observability`.
- Codestra-Prometheus scrapes Telnexa's private `/metrics` endpoint.
- Codestra-Alertmanager delivers alert transitions to Middleware.
- Middleware owns alert ingestion, runtime observation/heartbeat ingestion,
  incident policy, KPI aggregation, and every Odoo write.
- Grafana, Loki, Tempo, Superset and OpenBao keep their read-only/secret-only
  responsibilities.

Telnexa does not accept monitoring writes and does not hold an Odoo credential.

## Route map

| Producer or consumer | Canonical route | Direction | Authority |
| --- | --- | --- | --- |
| Codestra-Prometheus | `/metrics` | scrape Telnexa | Prometheus |
| Codestra-Alertmanager | `POST /v1/integrations/alertmanager/events` | monitoring → Middleware | Alertmanager/Middleware |
| Codestra-Alertmanager | `POST /v1/integrations/alertmanager/status-events` | monitoring → Middleware | Alertmanager/Middleware |
| Middleware | `POST /api/v1/events/telnexa` | Telnexa outbox → Middleware | Middleware |
| Middleware | `POST /platform/v1/runtime/observations` | collector → Middleware | Middleware |
| Middleware | `POST /platform/v1/telemetry/heartbeats` | collector → Middleware | Middleware |
| Middleware | `POST /platform/v1/services/{service_id}/contract-refresh` | service contract → Middleware | Middleware |
| Middleware | `GET /platform/v1/services/{service_id}/coverage` | Middleware reads coverage | Middleware |
| Middleware → Odoo | Odoo JSON-2 adapter | Middleware → Odoo | Middleware only |

The two Alertmanager paths are Middleware-owned routes. They are not Telnexa
routes and must not be added to this service.

## Monitoring repository ownership

| Repository | Responsibility for Telnexa |
| --- | --- |
| Codestra-Prometheus | Scrape `/metrics`, calculate recording rules and alert expressions |
| Codestra-Alertmanager | Send native v4 firing/resolved and status events to Middleware |
| Codestra-Grafana | Read-only dashboards through the authenticated Middleware/BFF |
| Codestra-Telemetry | Telemetry policy and signal definitions |
| Codestra-Alloy | Collect and forward logs/traces/OTLP telemetry |
| Codestra-Loki | Store/query logs through Middleware |
| Codestra-Tempo | Store/query traces through Middleware |
| Codestra-Node-Exporter | Host metrics scraped by Prometheus |
| Codestra-cAdvisor | Container metrics scraped by Prometheus |
| Codestra-Redis-Exporter | Redis metrics scraped by Prometheus |
| Codestra-Blackbox-Exporter | Approved endpoint probes |
| Codestra-Postgres-Exporter | PostgreSQL metrics scraped by Prometheus |
| Superset | Read-only business analytics |
| Codestra-OpenBao | Secrets and cryptographic material only |

## Odoo boundary

Middleware may map approved events and normalized KPI snapshots into Odoo
through its existing Odoo JSON-2 adapter. Telnexa emits business events to
Middleware's signed outbox route and exposes no Odoo model, database, token,
JSON-2 URL, or direct write helper.

The following data may cross into Odoo only after Middleware policy and
idempotency:

- normalized SMS delivery and failure KPIs;
- usage, billing and reconciliation summaries;
- approved operational incident state;
- Middleware↔Telnexa sync health;
- replay/reconciliation evidence summaries.

Raw Prometheus samples, raw logs, raw traces, customer message bodies, provider
secrets and OpenBao values do not belong in Odoo.

## Authentication and anti-replay

- The Telnexa metadata routes use the existing tenant-bound OIDC/API-key/service-account authentication and the `sms.health.read` scope.
- `/metrics` remains private and uses the existing bearer token file.
- Telnexa → Middleware business events keep the existing mTLS + bearer + raw-body HMAC + idempotency contract.
- Alertmanager → Middleware uses Middleware's native webhook v4 identity and its own replay/idempotency controls.
- All observations and heartbeats require Middleware's bounded idempotency, correlation, source-sequence and freshness rules.

## Example

```http
GET /api/v1/integration/observability HTTP/1.1
Host: api.telnexa.co
X-Tenant-ID: <tenant>
Authorization: Bearer <service-token>
```

The response contains release identity, service endpoints, signal owners and
write-boundary declarations; it never contains credentials or customer data.

## Acceptance checks

1. Fetch the Telnexa service contract with a service identity and verify the
   repository/source/image/config identity.
2. Confirm Prometheus can privately scrape `/metrics` with the metrics token.
3. Confirm Alertmanager's native v4 event and status routes reach Middleware.
4. Confirm Middleware registers `telnexa-billing-api` and reads coverage.
5. Confirm Middleware's Odoo adapter receives only normalized approved summaries.
6. Verify no component other than Middleware can mutate Odoo.
7. Keep runtime activation and live SMS delivery as separate approval gates.
