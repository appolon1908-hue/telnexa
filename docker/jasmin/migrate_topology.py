#!/usr/bin/env python3
"""Migrate only empty, known Jasmin RabbitMQ entities to durable topology."""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


FIXED_QUEUES = {
    "RouterPB_bill_request_submit_sm_resp_all",
    "RouterPB_deliver_sm_all",
    "deliver_sm_thrower",
    "dlr_thrower",
}
DYNAMIC_QUEUE = re.compile(r"(?:DLRLookup-|submit\.sm\.)[A-Za-z0-9_.-]{1,128}")
JASMIN_EXCHANGES = {"billing", "messaging"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def is_jasmin_queue(name: str) -> bool:
    return name in FIXED_QUEUES or DYNAMIC_QUEUE.fullmatch(name) is not None


def legacy_exchange_names(exchanges: list[dict[str, Any]]) -> list[str]:
    return sorted(
        exchange["name"]
        for exchange in exchanges
        if exchange.get("name") in JASMIN_EXCHANGES and exchange.get("durable") is False
    )


def source_bindings_path(exchange: str) -> str:
    encoded = urllib.parse.quote(exchange, safe="")
    return f"/exchanges/%2F/{encoded}/bindings/source"


def evaluate_topology(
    queues: list[dict[str, Any]],
    exchanges: list[dict[str, Any]],
    bindings_by_exchange: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    legacy_queues = [
        queue
        for queue in queues
        if isinstance(queue.get("name"), str)
        and is_jasmin_queue(queue["name"])
        and queue.get("durable") is False
    ]
    legacy_queue_names = {queue["name"] for queue in legacy_queues}
    legacy_exchanges = legacy_exchange_names(exchanges)
    for exchange in legacy_exchanges:
        for binding in bindings_by_exchange.get(exchange, []):
            destination = binding.get("destination")
            if (
                binding.get("destination_type") != "queue"
                or not isinstance(destination, str)
                or destination not in legacy_queue_names
            ):
                raise RuntimeError(
                    f"refusing binding outside the selected legacy topology on {exchange}"
                )
    return legacy_queues, legacy_exchanges


class RabbitManagement:
    def __init__(self) -> None:
        raw_url = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672/api")
        parsed = urllib.parse.urlsplit(raw_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "rabbitmq"
            or parsed.port != 15672
            or parsed.path.rstrip("/") != "/api"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("RABBITMQ_MANAGEMENT_URL must be the private RabbitMQ API")
        self.base_url = raw_url.rstrip("/")
        username = os.getenv("RABBITMQ_USER", "")
        password = os.getenv("RABBITMQ_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("RabbitMQ migration authority is not configured")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.authorization = f"Basic {encoded}"

    def request(self, method: str, path: str) -> Any:
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers={"Authorization": self.authorization, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError("RabbitMQ management response is oversized")
            return json.loads(body) if body else None

    def wait_ready(self) -> None:
        for _ in range(30):
            try:
                self.request("GET", "/overview")
                return
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                time.sleep(1)
        raise RuntimeError("RabbitMQ management API did not become ready")


def main() -> None:
    mode = os.getenv("MIGRATION_MODE", "apply")
    if mode not in {"check", "apply"}:
        raise RuntimeError("MIGRATION_MODE must be check or apply")
    client = RabbitManagement()
    client.wait_ready()
    queues = client.request("GET", "/queues/%2F")
    exchanges = client.request("GET", "/exchanges/%2F")
    if not isinstance(queues, list) or not isinstance(exchanges, list):
        raise RuntimeError("RabbitMQ topology response is invalid")
    bindings = {
        exchange: client.request("GET", source_bindings_path(exchange))
        for exchange in legacy_exchange_names(exchanges)
    }
    if any(not isinstance(value, list) for value in bindings.values()):
        raise RuntimeError("RabbitMQ binding response is invalid")
    legacy_queues, legacy_exchanges = evaluate_topology(queues, exchanges, bindings)
    if not legacy_queues and not legacy_exchanges:
        print("Jasmin RabbitMQ topology is migration-clean")
        return
    if os.getenv("JASMIN_DURABILITY_MIGRATION_AUTHORIZED", "false") != "true":
        raise RuntimeError(
            "legacy Jasmin topology requires explicit durability migration authorization"
        )
    nonempty = [queue["name"] for queue in legacy_queues if int(queue.get("messages", 0)) != 0]
    if nonempty:
        raise RuntimeError(
            "refusing durability migration while known Jasmin queues contain messages: "
            + ",".join(sorted(nonempty))
        )
    if mode == "check":
        print(
            "Jasmin RabbitMQ durability migration authorized; "
            f"queues={len(legacy_queues)} exchanges={len(legacy_exchanges)}"
        )
        return
    busy = [queue["name"] for queue in legacy_queues if int(queue.get("consumers", 0)) != 0]
    if busy:
        raise RuntimeError(
            "refusing durability migration while known Jasmin queues are non-empty or consumed: "
            + ",".join(sorted(busy))
        )
    for queue in legacy_queues:
        client.request("DELETE", f"/queues/%2F/{urllib.parse.quote(queue['name'], safe='')}")
    for exchange in legacy_exchanges:
        remaining = client.request("GET", source_bindings_path(exchange))
        if remaining:
            raise RuntimeError(f"refusing to delete bound legacy exchange {exchange}")
        client.request("DELETE", f"/exchanges/%2F/{urllib.parse.quote(exchange, safe='')}")
    print(
        "Migrated empty Jasmin RabbitMQ topology; "
        f"queues={len(legacy_queues)} exchanges={len(legacy_exchanges)}"
    )


if __name__ == "__main__":
    main()
