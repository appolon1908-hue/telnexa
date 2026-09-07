from prometheus_client import Counter, Gauge, Histogram

DISPATCH_JOBS = Gauge("telnexa_sms_dispatch_jobs", "Durable SMS dispatch jobs", ["state"])
DISPATCH_OLDEST = Gauge("telnexa_sms_dispatch_oldest_seconds", "Oldest eligible dispatch age")
DISPATCH_ATTEMPTS = Counter(
    "telnexa_sms_dispatch_attempts_total", "Dispatch attempts", ["provider", "outcome"]
)
SUBMIT_LATENCY = Histogram(
    "telnexa_sms_provider_submit_latency_seconds", "Provider submit latency", ["provider"]
)
PROVIDER_CIRCUIT = Gauge(
    "telnexa_sms_provider_circuit_state", "Provider circuit state", ["provider"]
)
PROVIDER_HEALTH = Gauge("telnexa_sms_provider_health_score", "Provider health score", ["provider"])
PROVIDER_INFLIGHT = Gauge(
    "telnexa_sms_provider_inflight", "Provider in-flight submissions", ["provider"]
)
PROVIDER_THROTTLE = Counter(
    "telnexa_sms_provider_throttle_total", "Provider throttles", ["provider"]
)
SUBMISSION_UNKNOWN = Gauge("telnexa_sms_submission_unknown_total", "Unknown submissions")
DLR_EVENTS = Counter("telnexa_sms_dlr_events_total", "DLR events", ["status"])
DLR_LAG = Histogram("telnexa_sms_dlr_lag_seconds", "DLR receive lag", ["provider"])
MO_EVENTS = Counter("telnexa_sms_mo_events_total", "MO events", ["type"])
UNMATCHED_EVENTS = Gauge("telnexa_sms_unmatched_provider_events", "Unmatched provider events")
MIDDLEWARE_OUTBOX = Gauge("telnexa_sms_middleware_outbox", "Middleware outbox", ["state"])
CUSTOMER_WEBHOOKS = Gauge("telnexa_sms_customer_webhooks", "Customer webhooks", ["state"])
BILLING_DRIFT = Gauge("telnexa_sms_billing_reconciliation_drift", "Billing reconciliation drift")
CANARY_REMAINING = Gauge("telnexa_sms_canary_remaining", "Remaining canary submissions")
