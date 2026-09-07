# Step 4 — Unknown-Outcome Reconciliation Evidence

## Binding rule

A provider timeout is not proof of failure. Once Telnexa begins a provider submission, the durable operation consumes its single submission attempt before invoking the transport.

```text
provider submission attempts = 1
blind provider resubmissions = 0
unknown state = reconciliation_required
read-back maximum = 3
unresolved terminal state = manual_review
```

## Isolated proof journey

The disposable Jasmin simulator persists a synthetic acceptance and then returns HTTP 503. The provider must:

1. retain the billing reservation;
2. enter `reconciliation_required`;
3. return the original operation on exact idempotent replay;
4. reject changed content under the same idempotency key;
5. perform two failed read-backs and one successful authoritative read-back;
6. commit the reservation only after the authoritative result;
7. preserve exactly one provider submission attempt;
8. record zero provider resubmissions.

The same lab proves DLR replay behavior, changed-content replay rejection, delivered-state monotonicity, inbound STOP/HELP normalization, signed callback construction, and zero carrier connections/SMS sends.

## Billing rule

- Provider rejection before acceptance releases the reservation.
- Provider acceptance commits the reservation.
- Unknown outcomes retain the reservation until read-back resolves.
- Failed authoritative read-back releases the reservation.
- An unresolved outcome enters manual review rather than creating another provider attempt.

## Evidence markers

```text
TELNEXA_SMS_UNKNOWN_OUTCOME_NO_RETRY=PASS
TELNEXA_SMS_RECONCILIATION_READBACK=PASS
TELNEXA_SMS_RECONCILIATION_NO_RESUBMIT=PASS
TELNEXA_SMS_ZERO_EXTERNAL_EFFECT=PASS
```

These markers are valid only when emitted by the exact-head GitHub workflow and associated with the reviewed PR head.
