# Issue 29: real cross-repository read-back laboratory

Telnexa PR #30 and Middleware PR #158 are merged source repairs. This lab adds
proof that the real Middleware adapter can consume the actual Telnexa API, not
only a mock response. It does not activate machine identity, providers or live SMS.

The workflow pins Middleware to accepted source
`882f7378a27ca0a87c1cb496c5d24e73984caf65` and tests Telnexa's exact PR source and
synthetic merge result separately. Each repository runs with its real dependencies
in a separate interpreter. Middleware uses its checked-in hash-locked runtime
requirements, validated Settings and actual CommandExecutionRequest type.

The runner first executes the existing disposable PostgreSQL certification of
atomic acceptance receipts, exact/altered replay, quota, signed callback v2,
DLR processing and local STOP. It then starts the actual Telnexa ASGI application
on an ephemeral literal-loopback socket and invokes Middleware in a subprocess.
A newly generated disposable scoped API key travels only through stdin, never
arguments, source, console output or evidence artifacts.

The additional cases prove: delivered-message read-back using stable Telnexa
identity; missing record without POST fallback; changed request fingerprint,
tenant, correlation and key denial; and disabled delivery rejection before HTTP.
An observing HTTP client refuses writes but passes every permitted GET through
the real HTTP transport. No response bodies or application modules are replaced.
A before/after database digest checks messages, dispatch jobs, acceptance receipts,
wallets and outbox state. Authentication bookkeeping is not confused with a
message/provider side effect. All dispatch attempt counts must remain zero.

The test refuses any database except the explicitly authorized dedicated loopback
CI database and requires all seven delivery/umbrella flags to be literal false.
Python socket egress is restricted to loopback; no provider or outbox worker runs.
The API process is terminated in a finally block, and the CI service database is
disposable. Evidence contains source SHAs, booleans, counts and named PASS cases,
not credentials or customer/fixture message payloads.

## Boundaries that remain real deployment requirements

This proves source/protocol interoperability over local HTTP. It does not prove
private production DNS, mTLS certificates, secret-manager bindings, deployed
image digests/signatures, carrier reachability, end-to-end downstream webhook
intake or provider-service rollback. The lab does not use fabricated image IDs.
`staging_runtime_certified` and `production_certified` remain false in its report.

Issue #29 must remain open until the actual API+relay+Middleware immutable image
tuple is admitted, private/mTLS/secret references and inbound ownership are
verified, the approved synthetic staging canary and rollback rehearsal have
immutable evidence, and any real destination activation is independently bounded
and authorized. Existing `docs/ISSUE29_PRIVATE_SMS_CERTIFICATION.md` remains the
rollout authority, including the coordinated API/relay HMAC v2 requirement.
