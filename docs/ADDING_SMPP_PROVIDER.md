# Adding an SMPP provider

Do not add a production route until the carrier bind is confirmed. Obtain the carrier's host, port, system ID, password, bind mode, source/destination TON and NPI, TPS limit, sender-ID policy, destination coverage, reconnect policy, DLR format, and IP allowlist requirements. Store the populated values outside Git; `examples/smpp-provider.env.example` is the field checklist.

## 1. Create the connector

Open `./scripts/console.sh`, authenticate with the admin values from `.env`, and enter:

```text
smppccm -a
cid carrier_primary
host REAL_SMPP_HOST
port REAL_SMPP_PORT
username REAL_SYSTEM_ID
password REAL_SMPP_PASSWORD
bind transceiver
source_addr_ton 0
source_addr_npi 0
dest_addr_ton 1
dest_addr_npi 1
submit_throughput 10
reconnectOnConnectionFailure yes
reconnectOnConnectionLoss yes
reconnectOnConnectionFailureDelay 10
sessionInitTimerSecs 30
enquireLinkTimerSecs 30
ok
smppccm -1 carrier_primary
smppccm -l
persist
```

The values above are examples, not universal carrier settings. Use `bind transceiver`, `transmitter`, or `receiver` exactly as required. Inspect `docker compose logs jasmin` and require a bound state before routing.

## 2. Configure inbound MO delivery

Create the internal HTTP connector and MO route:

```text
httpccm -a
cid middleware_mo
url http://webhook-relay:8080/events/inbound
method POST
ok
morouter -a
type DefaultRoute
connector http(middleware_mo)
ok
persist
```

For multiple providers or customers, add connector/source/destination filters and higher-order static routes before the default route.

## 3. Configure outbound routing

For one carrier:

```text
mtrouter -a
type DefaultRoute
connector smppc(carrier_primary)
rate 0
ok
persist
```

Apply destination, group, user, and source-address filters before static routes when traffic must be segmented. Enforce the carrier TPS both with `submit_throughput` and customer/user quotas. Sender IDs must be restricted per carrier and customer; do not assume arbitrary alphanumeric senders are permitted.

## 4. Delivery receipts

Outbound middleware requests should set `dlr=yes`, `dlr-level=2` (SMSC receipt) or `3` (submit plus SMSC receipt), `dlr-method=POST`, and `dlr-url=http://webhook-relay:8080/events/dlr`. The relay signs and forwards callbacks to `<WEBHOOK_TARGET_BASE_URL>/webhooks/sms/dlr`. Treat `UNDELIV`, `REJECTD`, `EXPIRED`, and equivalent terminal states as failed events in middleware; the relay also exposes `/events/failed` for normalized failure producers.

## 5. Unicode, multipart, and sender rules

Use `coding=8` for UCS-2 Unicode. Jasmin segments long messages when long-content authorization is enabled. The default middleware identity retains long-content authorization, but carrier segment limits and billing must be enforced in middleware. Add `mt_messaging_cred` source-address and destination regex filters per customer and validate sender IDs before submission.

## 6. Test safely

First run inspection only:

```bash
./scripts/provider-test.sh carrier_primary
```

Only after the carrier confirms the bind, routing, sender, and an authorized destination:

```bash
./scripts/provider-test.sh carrier_primary --send +4915XXXXXXXX
```

The second command sends a real SMS. Never run it without authorization. A successful Jasmin submission is not proof of handset delivery; verify the final DLR.

## 7. Failover

Create and bind `carrier_secondary` using the same process. Replace the default route with a `FailoverMTRoute` containing `smppc(carrier_primary)` followed by `smppc(carrier_secondary)`. Both connectors in a failover route must use identical filters. Validate carrier-specific sender and destination compatibility, then persist. Use `RandomRoundrobinMTRoute` only for intentional load distribution, not ordered failover.
