# Step 4 — SMS Provider Production Gates

Source completion does not authorize carrier activation.

Before production, all of the following require separate evidence and approval:

- protected merge of SDK, Middleware, and Telnexa source authorities;
- immutable Telnexa image, SBOM, provenance, signature, and vulnerability acceptance;
- OpenBao-backed Middleware and provider identities;
- mTLS and private network binding;
- carrier sandbox submit, DLR, and MO evidence;
- billing reconciliation and restore rehearsal;
- Keycloak/Kong scope and audience tests;
- public native-port denial;
- staging soak, capacity, rate-limit, and failure tests;
- rollback rehearsal;
- explicit production go/no-go and change window.

Current source safety state:

```text
SMS_DELIVERY=false
LIVE_SMS_DELIVERY=false
JASMIN_LIVE_SUBMISSION=false
CALLBACK_DISPATCH_ENABLED=false
REAL_CARRIER_CREDENTIALS_INSTALLED=NO
PRODUCTION_DEPLOYED=NO
SMS_SENT=NO
```
