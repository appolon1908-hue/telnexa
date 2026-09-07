from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = spec_from_file_location(
    "jasmin_topology_migration", ROOT / "docker/jasmin/migrate_topology.py"
)
assert SPEC and SPEC.loader
migration = module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_migration_selects_only_known_legacy_jasmin_entities():
    queues = [
        {"name": "dlr_thrower", "durable": False, "messages": 0, "consumers": 0},
        {"name": "submit.sm.primary", "durable": False, "messages": 0, "consumers": 0},
        {"name": "unrelated", "durable": False, "messages": 0, "consumers": 0},
    ]
    exchanges = [
        {"name": "messaging", "durable": False},
        {"name": "unrelated", "durable": False},
    ]
    bindings = {
        "messaging": [
            {"destination_type": "queue", "destination": "dlr_thrower"},
            {"destination_type": "queue", "destination": "submit.sm.primary"},
        ],
        "billing": [],
    }
    selected_queues, selected_exchanges = migration.evaluate_topology(queues, exchanges, bindings)
    assert [queue["name"] for queue in selected_queues] == [
        "dlr_thrower",
        "submit.sm.primary",
    ]
    assert selected_exchanges == ["messaging"]


def test_migration_rejects_unknown_exchange_binding():
    with pytest.raises(RuntimeError, match="outside the selected legacy topology"):
        migration.evaluate_topology(
            [],
            [{"name": "messaging", "durable": False}],
            {"messaging": [{"destination_type": "queue", "destination": "customer-data"}]},
        )


def test_migration_rejects_binding_to_known_but_durable_queue_before_deletion():
    with pytest.raises(RuntimeError, match="outside the selected legacy topology"):
        migration.evaluate_topology(
            [{"name": "dlr_thrower", "durable": True, "messages": 0, "consumers": 0}],
            [{"name": "messaging", "durable": False}],
            {"messaging": [{"destination_type": "queue", "destination": "dlr_thrower"}]},
        )


def test_binding_discovery_uses_only_existing_legacy_exchanges():
    assert migration.legacy_exchange_names(
        [
            {"name": "messaging", "durable": False},
            {"name": "billing", "durable": True},
            {"name": "unrelated", "durable": False},
        ]
    ) == ["messaging"]


def test_binding_discovery_uses_rabbit_management_source_endpoint():
    assert migration.source_bindings_path("messaging") == "/exchanges/%2F/messaging/bindings/source"


@pytest.mark.parametrize(
    "name",
    [
        "RouterPB_bill_request_submit_sm_resp_all",
        "RouterPB_deliver_sm_all",
        "deliver_sm_thrower",
        "dlr_thrower",
        "DLRLookup-main",
        "submit.sm.connector-1",
    ],
)
def test_known_queue_contract(name: str):
    assert migration.is_jasmin_queue(name)
