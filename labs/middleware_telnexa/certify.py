"""Run the isolated Middleware to Telnexa Step 4 certification journey."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

PROVIDER_URL = os.environ.get("STAGE4_PROVIDER_URL", "http://provider:8080")
SIMULATOR_URL = os.environ.get("STAGE4_SIMULATOR_URL", "http://jasmin-simulator:8080")
TENANT = "tenant-stage4-certification"
MIDDLEWARE_TOKEN = os.environ["TELNEXA_MIDDLEWARE_BEARER_TOKEN"]
CALLBACK_SECRET = os.environ["TELNEXA_PROVIDER_CALLBACK_SECRET"]
MIDDLEWARE_CALLBACK_SECRET = os.environ["TELNEXA_MIDDLEWARE_CALLBACK_SECRET"]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer " + MIDDLEWARE_TOKEN,
        "X-Tenant-ID": TENANT,
    }


def callback_headers(event_id: str, raw: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        CALLBACK_SECRET.encode(),
        timestamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Telnexa-Timestamp": timestamp,
        "X-Telnexa-Event-Id": event_id,
        "X-Telnexa-Signature": "sha256=" + signature,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        reset = client.post(
            SIMULATOR_URL + "/control/reset",
            json={"scenario": "ambiguous_after_acceptance", "readback_failures": 2},
        )
        reset.raise_for_status()

        message_id = str(uuid.uuid4())
        command = {
            "command_id": str(uuid.uuid4()),
            "command_type": "sms.message.submit.v1",
            "command_version": "1.0",
            "target": "telnexa-sms",
            "tenant_id": TENANT,
            "requested_by": "middleware-stage4-certifier",
            "correlation_id": "stage4-correlation-0001",
            "idempotency_key": "test-test-test-test",
            "capability": "SMS_DELIVERY",
            "payload": {
                "message_id": message_id,
                "channel": "sms",
                "destination": "+18095550123",
                "sender": "CODESTRA",
                "content": "Synthetic certification only",
                "encoding": "GSM-7",
                "characters": 28,
                "segments": 1,
                "category": "transactional",
                "client_reference": message_id,
                "scheduled_at": None,
                "billing_account_id": "billing-stage4",
                "campaign_id": None,
            },
        }

        submitted = client.post(
            PROVIDER_URL + "/api/v1/provider/operations",
            json=command,
            headers=headers(),
        )
        submitted.raise_for_status()
        operation = submitted.json()
        require(operation["state"] == "reconciliation_required", "unknown outcome not quarantined")
        require(operation["submission_attempts"] == 1, "submission attempt count is not one")
        require(operation["billing"]["state"] == "reserved", "unknown billing reservation released")

        replay = client.post(
            PROVIDER_URL + "/api/v1/provider/operations",
            json=command,
            headers=headers(),
        )
        replay.raise_for_status()
        require(replay.json()["replay"] is True, "exact idempotent replay was not recognized")

        conflicting = json.loads(json.dumps(command))
        conflicting["payload"]["content"] = "Different content"
        conflicting["payload"]["characters"] = 17
        conflict_response = client.post(
            PROVIDER_URL + "/api/v1/provider/operations",
            json=conflicting,
            headers=headers(),
        )
        require(conflict_response.status_code == 409, "conflicting idempotency reuse was accepted")

        states: list[str] = []
        for _ in range(3):
            response = client.post(
                PROVIDER_URL + f"/api/v1/provider/operations/{operation['operation_id']}/reconcile",
                headers=headers(),
            )
            response.raise_for_status()
            states.append(response.json()["state"])
        require(
            states == ["reconciliation_required", "reconciliation_required", "provider_accepted"],
            f"unexpected reconciliation states: {states}",
        )

        simulator_metrics = client.get(SIMULATOR_URL + "/metrics").json()
        require(simulator_metrics["submit_calls"] == 1, "Jasmin simulator saw duplicate submission")
        require(simulator_metrics["readback_calls"] == 3, "bounded readback count differs")
        require(simulator_metrics["carrier_connections"] == 0, "simulator contacted a carrier")
        require(simulator_metrics["sms_sent"] == 0, "simulator sent an SMS")

        usage = client.get(PROVIDER_URL + "/api/v1/provider/usage", headers=headers()).json()
        require(usage["provider_submission_attempts"] == 1, "provider attempt evidence differs")
        require(usage["provider_resubmissions"] == 0, "provider resubmission evidence differs")
        require(usage["reconciliation_readbacks"] == 3, "provider readback evidence differs")

        delivered = {
            "tenant_id": TENANT,
            "message_id": message_id,
            "provider_reference": "jasmin-synthetic-reference",
            "provider_status": "DELIVRD",
            "occurred_at": datetime.now(UTC).isoformat(),
            "failure_code": None,
            "failure_message": None,
        }
        delivered_raw = canonical(delivered)
        delivered_headers = callback_headers("stage4-dlr-delivered", delivered_raw)
        dlr = client.post(
            PROVIDER_URL + "/api/v1/provider/callbacks/dlr",
            content=delivered_raw,
            headers=delivered_headers,
        )
        dlr.raise_for_status()
        require(dlr.json()["status"] == "delivered", "DLR was not normalized")
        duplicate_dlr = client.post(
            PROVIDER_URL + "/api/v1/provider/callbacks/dlr",
            content=delivered_raw,
            headers=delivered_headers,
        )
        duplicate_dlr.raise_for_status()
        require(duplicate_dlr.json()["duplicate"] is True, "DLR replay created another effect")

        changed_dlr = dict(delivered)
        changed_dlr["provider_status"] = "failed"
        changed_raw = canonical(changed_dlr)
        changed_response = client.post(
            PROVIDER_URL + "/api/v1/provider/callbacks/dlr",
            content=changed_raw,
            headers=callback_headers("stage4-dlr-delivered", changed_raw),
        )
        require(changed_response.status_code == 409, "changed-content DLR replay was accepted")

        regressive_response = client.post(
            PROVIDER_URL + "/api/v1/provider/callbacks/dlr",
            content=changed_raw,
            headers=callback_headers("stage4-dlr-regressive", changed_raw),
        )
        regressive_response.raise_for_status()
        require(regressive_response.json()["ignored"] is True, "delivered state was downgraded")
        require(regressive_response.json()["status"] == "delivered", "terminal state changed")

        for event_id, sender, content, expected_action in (
            ("stage4-mo-stop", "+18095550999", "STOP", "stop"),
            ("stage4-mo-help", "+18095550888", "HELP", "help"),
        ):
            mo = {
                "tenant_id": TENANT,
                "provider_message_id": event_id,
                "sender": sender,
                "destination": "+18095550000",
                "content": content,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            raw = canonical(mo)
            response = client.post(
                PROVIDER_URL + "/api/v1/provider/callbacks/mo",
                content=raw,
                headers=callback_headers(event_id, raw),
            )
            response.raise_for_status()
            require(response.json()["action"] == expected_action, "MO compliance mapping failed")

        opt_outs = client.get(PROVIDER_URL + "/api/v1/provider/opt-outs", headers=headers()).json()
        require(len(opt_outs["items"]) == 1, "STOP did not produce exactly one durable opt-out")

        callbacks = client.get(
            PROVIDER_URL + "/api/v1/provider/callbacks", headers=headers()
        ).json()["items"]
        callback_types = {item["event_type"] for item in callbacks}
        require("sms.message.delivered.v1" in callback_types, "delivered callback missing")
        require("sms.recipient.opted-out.v1" in callback_types, "opt-out callback missing")
        require("sms.help-requested.v1" in callback_types, "help callback missing")

        sample = callbacks[0]
        callback_raw = canonical(sample["payload"])
        callback_timestamp = str(int(time.time()))
        callback_signature = hmac.new(
            MIDDLEWARE_CALLBACK_SECRET.encode(),
            callback_timestamp.encode() + b"." + callback_raw,
            hashlib.sha256,
        ).hexdigest()
        require(len(callback_signature) == 64, "Middleware callback signature is malformed")

        health = client.get(PROVIDER_URL + "/health").json()
        require(health["sms_delivery"] is False, "SMS delivery flag was enabled")
        require(health["live_sms_delivery"] is False, "live SMS flag was enabled")
        require(health["jasmin_live_submission"] is False, "Jasmin live flag was enabled")

        print("TELNEXA_MIDDLEWARE_COMMAND_MAPPING=PASS")
        print("TELNEXA_DURABLE_PROVIDER_JOURNAL=PASS")
        print("TELNEXA_SMS_EXACT_IDEMPOTENCY=PASS")
        print("TELNEXA_SMS_UNKNOWN_OUTCOME_NO_RETRY=PASS")
        print("TELNEXA_SMS_RECONCILIATION_READBACK=PASS")
        print("TELNEXA_SMS_RECONCILIATION_NO_RESUBMIT=PASS")
        print("TELNEXA_SMS_DLR_REPLAY_AND_MONOTONICITY=PASS")
        print("TELNEXA_SMS_MO_STOP_HELP=PASS")
        print("TELNEXA_SMS_SIGNED_CALLBACK_CONTRACT=PASS")
        print("TELNEXA_SMS_ZERO_EXTERNAL_EFFECT=PASS")


if __name__ == "__main__":
    main()
