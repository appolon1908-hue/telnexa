#!/usr/bin/env python3
"""Fail-closed validation of the SDK/Middleware/Telnexa Step 4 source lock."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str) -> None:
    raise SystemExit("STEP4_SMS_CONTRACT_LOCK=FAIL: " + message)


def git_head(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        fail(f"cannot resolve Git head for {path}: {error}")
    raise AssertionError("unreachable")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail(f"cannot load JSON {path}: {error}")
    raise AssertionError("unreachable")


def require_text(path: Path, values: list[str]) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read {path}: {error}")
    missing = [value for value in values if value not in content]
    if missing:
        fail(f"{path} is missing required contract tokens: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telnexa-dir", type=Path, default=Path.cwd())
    parser.add_argument("--middleware-dir", type=Path, required=True)
    parser.add_argument("--sdk-dir", type=Path, required=True)
    args = parser.parse_args()

    telnexa = args.telnexa_dir.resolve()
    middleware = args.middleware_dir.resolve()
    sdk = args.sdk_dir.resolve()
    lock = load_json(telnexa / "config/step4-sms-contract-lock.json")

    middleware_sha = lock["middleware"]["source_sha"]
    sdk_sha = lock["sdk"]["source_sha"]
    if not SHA.fullmatch(middleware_sha) or not SHA.fullmatch(sdk_sha):
        fail("external source identities must be full lowercase Git SHAs")
    if git_head(middleware) != middleware_sha:
        fail("checked-out Middleware SHA differs from the contract lock")
    if git_head(sdk) != sdk_sha:
        fail("checked-out SDK SHA differs from the contract lock")

    command_schema = load_json(middleware / lock["middleware"]["command_schema_path"])
    manifest = load_json(middleware / lock["middleware"]["connector_manifest_path"])
    command = lock["command"]
    properties = command_schema["allOf"][1]["properties"]
    if properties["command_type"].get("const") != command["command_type"]:
        fail("Middleware command type differs from the lock")
    if properties["target"].get("const") != command["target"]:
        fail("Middleware target differs from the lock")
    if properties["capability"].get("const") != command["capability"]:
        fail("Middleware capability differs from the lock")
    payload = properties["payload"]
    if payload.get("additionalProperties") is not False:
        fail("Middleware SMS payload is not fail-closed")
    if set(payload.get("required", [])) != set(command["payload_fields"]):
        fail("Middleware SMS payload field set differs from the lock")

    if manifest.get("connector_id") != "telnexa-sms":
        fail("Middleware connector manifest is not Telnexa SMS")
    if manifest.get("enabled_by_default") is not False:
        fail("Telnexa connector is enabled by default")
    command_policy = manifest["commands"][0]
    if command_policy.get("readback_required") is not True:
        fail("Telnexa connector does not require provider read-back")
    if command_policy["retry_policy"].get("unknown_outcome_requires_readback") is not True:
        fail("unknown outcome does not fail closed to read-back")
    manifest_events = {item["event_type"] for item in manifest.get("events", [])}
    if not set(lock["provider_events"]).issubset(manifest_events):
        fail("Telnexa provider event catalog differs from Middleware")

    require_text(
        sdk / lock["sdk"]["openapi_path"],
        [
            "Canonical provider-neutral Communications API v1",
            "/v1/communications/messages:",
            "createCommunicationMessage",
            "IdempotencyKey",
            "enum: [email, sms, voice]",
            "indeterminate",
        ],
    )
    require_text(
        sdk / lock["sdk"]["asyncapi_path"],
        [
            "codestra.communications.message.status.v1",
            "codestra.communications.message.event.v1",
            "codestra.events.sms_received",
            "enum: [email, sms, voice]",
            "indeterminate",
        ],
    )

    require_text(
        telnexa / "billing/sms_provider_contracts.py",
        [
            f'SDK_CONTRACT_SHA = "{sdk_sha}"',
            f'MIDDLEWARE_SMS_SHA = "{middleware_sha}"',
            'Literal["sms.message.submit.v1"]',
            'Literal["telnexa-sms"]',
            'Literal["SMS_DELIVERY"]',
        ],
    )
    require_text(
        telnexa / "billing/sms_provider_submission.py",
        ['"provider_resubmissions": 0'],
    )
    require_text(
        telnexa / "billing/sms_provider_transport.py",
        ['name = "disabled"'],
    )
    require_text(
        telnexa / "billing/migrations/004_sms_provider_runtime.sql",
        [
            "submission_attempts >= 0 AND submission_attempts <= 1",
            "enforce_one_sms_provider_submission",
            "ENABLE ROW LEVEL SECURITY",
            "sms_provider_reconciliation_evidence",
        ],
    )
    require_text(
        telnexa / "labs/middleware_telnexa/compose.yml",
        [
            'SMS_DELIVERY: "false"',
            'LIVE_SMS_DELIVERY: "false"',
            'JASMIN_LIVE_SUBMISSION: "false"',
            "internal: true",
        ],
    )
    compose = (telnexa / "labs/middleware_telnexa/compose.yml").read_text(encoding="utf-8")
    if "ports:" in compose:
        fail("isolated certification lab publishes a host port")
    if any(value is not False for value in lock["safety"].values()):
        fail("one or more live-effect safety flags are enabled")
    if lock["unknown_outcome_rule"] != {
        "provider_submission_attempts": 1,
        "provider_resubmissions": 0,
        "readback_required": True,
        "maximum_readback_attempts": 3,
        "unresolved_state": "manual_review",
    }:
        fail("unknown-outcome rule differs from the approved rule")

    print("TELNEXA_SDK_COMMUNICATIONS_CONTRACT_LOCK=PASS")
    print("TELNEXA_MIDDLEWARE_SMS_COMMAND_LOCK=PASS")
    print("TELNEXA_MIDDLEWARE_SMS_EVENT_LOCK=PASS")
    print("TELNEXA_UNKNOWN_OUTCOME_RULE_LOCK=PASS")
    print("TELNEXA_STEP4_LIVE_EFFECT_FLAGS_DISABLED=PASS")


if __name__ == "__main__":
    try:
        main()
    except KeyError as error:
        fail(f"required contract key is absent: {error}")
    except Exception as error:  # fail closed with one stable marker
        print(f"STEP4_SMS_CONTRACT_LOCK=FAIL: {error}", file=sys.stderr)
        raise
