# Central provider alert routing

Prometheus evaluates provider alerts. The opt-in compose.central-alerting.yml
overlay sends native API v2 alerts to the core private mTLS listener at
10.40.0.1:19093 with the verified alertmanager.core.codestra.internal server
identity. The base deployment remains usable before this private transport exists.
Native Alertmanager is not exposed publicly. Certificate validation is mandatory.
Supply ALERTMANAGER_CA_FILE, ALERTMANAGER_CLIENT_CERT_FILE, and
ALERTMANAGER_CLIENT_KEY_FILE as protected file paths; do not put values in Git.

Every provider rule supplies severity, environment, service, codestra_business,
owner, summary, description and runbook_url. The external prometheus label keeps
the two independently evaluated provider sources distinguishable.

## Prerequisites and installation

Verify the core TLS listener, its exact current image and configuration identity,
certificate chain/SAN/expiry, the distinct provider client identity and restricted
file permissions. Use promtool from the selected immutable Prometheus image.
Back up configuration and TSDB state with encrypted off-host restore evidence.
Render base Compose plus this overlay; confirm existing metrics credentials,
image, state volume, networks, scrape jobs and safety settings are preserved.
Recreate only Prometheus after protected source acceptance. Do not apply the
overlay while the destination or secret files are missing.

## Verify and investigate

Require /api/v1/alertmanagers to list the private HTTPS destination, all previous
scrape targets to remain healthy, and prometheus_notifications_errors_total to
remain unchanged while prometheus_notifications_sent_total increases.
Read the same alert labels in central Alertmanager and verify its selected
receiver. Verify durable Middleware incident IDs, transitions and deduplication
separately: the legacy monitoring receiver's hash ledger is not that proof.

For stalled delivery, inspect retry/dead-letter counters and the provider event
ledger, resolve the delivery error, then verify the counters and event read-back.
Do not replay messages or enable email/SMS merely to clear monitoring alerts.
Disk/CPU/memory alerts require host capacity checks before service changes.
Billing ambiguity requires provider reconciliation before retry.
No new alert source authorizes a business mutation.

## Rollback

Restore the recorded config and rule bytes and the previous Compose configuration,
using the same immutable image and state volume. Confirm all original scrape
targets and readiness. Preserve alert/incident audit records. The old absence of
an Alertmanager destination is a rollback limitation, never a delivery PASS.
