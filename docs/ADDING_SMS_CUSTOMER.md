# Adding an SMS customer

Customers should normally call the existing middleware, not Jasmin directly. The middleware supplies tenant authentication, idempotency, HMAC verification, billing, abuse controls, audit logs, and a stable API contract. Direct Jasmin HTTP/SMPP access is an exception requiring explicit network and commercial approval.

## Create a group and user

Open `./scripts/console.sh`:

```text
group -a
gid customer_acme
ok
user -a
username acme_api
password A_RANDOM_UNIQUE_PASSWORD
gid customer_acme
uid acme_api
ok
user -u acme_api
mt_messaging_cred quota http_throughput 5
mt_messaging_cred quota smpps_throughput 5
mt_messaging_cred quota balance 100.00
mt_messaging_cred quota submit_sm_count 10000
smpps_cred quota max_bindings 2
ok
persist
```

Generate unique credentials with a password manager or `openssl rand -base64 36`. Deliver them through an approved secret channel and never commit or log them.

## Restrictions and routing

Configure `mt_messaging_cred` authorization flags conservatively. Apply source-address regex filters for approved sender IDs and destination regex filters for contracted countries. Create group/user filters and a higher-priority MT route to the correct carrier; leave the default route only for explicitly accepted traffic. Set both Jasmin user throughput and upstream connector throughput.

## Billing and quotas

Jasmin balance and submit-count quotas are safety limits, not a full billing ledger. The middleware/PostgreSQL integration should reserve funds before submission, price multipart segments, reconcile DLRs, record refunds according to policy, enforce currency/tax rules, and expose auditable usage. Update quotas transactionally through an approved operator workflow.

## Abuse controls

- Verify customer identity and use case before activation.
- Enforce destination, sender, content, quiet-hour, and velocity policies in middleware.
- Rate-limit per tenant, user, source IP, destination, and campaign.
- Block premium/high-risk ranges unless explicitly contracted.
- Detect pumping, credential stuffing, repeated failures, and unusual geographic changes.
- Provide credential rotation and immediate suspension procedures.
- Retain message metadata according to privacy and regulatory policy; avoid message content in routine logs.

## Future customer SMPP

Port 2775 is not published by default. Prefer VPN/private connectivity. If direct SMPP is approved, add a narrowly bound host mapping and host firewall allowlist for fixed customer source IPs; never publish it globally. Configure maximum bindings, TPS, credentials, source/destination restrictions, and billing before opening access.
