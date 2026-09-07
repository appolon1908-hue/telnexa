"""Deterministic lost-insert recovery tests with no provider submission."""

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from test_product_api import seed
from test_product_api import clean as clean
from billing.message_idempotency import lock_message_key, recover_message_insert_race
from billing.models import Message, Sender, SmsDispatchJob, Wallet
from billing.schemas import SendRequest

api = importlib.import_module("billing.app")


def test_postgres_lock_is_transaction_scoped_and_keyed_by_tenant():
    db = Mock()
    db.get_bind.return_value.dialect.name = "postgresql"
    lock_message_key(db, "tenant-a", "same-key")
    first = db.execute.call_args
    assert "pg_advisory_xact_lock" in str(first.args[0])
    assert -(2**63) <= first.args[1]["lock_id"] < 2**63
    lock_message_key(db, "tenant-a", "same-key")
    assert db.execute.call_args.args[1] == first.args[1]
    lock_message_key(db, "tenant-b", "same-key")
    assert db.execute.call_args.args[1] != first.args[1]
    lock_message_key(db, "tenant-a", "other-key")
    assert db.execute.call_args.args[1] != first.args[1]


def test_offline_sqlite_does_not_execute_postgres_lock():
    db = Mock()
    db.get_bind.return_value.dialect.name = "sqlite"
    lock_message_key(db, "tenant-a", "same-key")
    db.execute.assert_not_called()


def test_recovery_rolls_back_before_winner_lookup_and_preserves_unrelated_error():
    events = []
    error = IntegrityError("INSERT", {}, RuntimeError("unrelated constraint"))
    db = SimpleNamespace(
        rollback=lambda: events.append("rollback"),
        scalar=lambda statement: events.append("lookup") or None,
    )
    with pytest.raises(IntegrityError) as raised:
        recover_message_insert_race(db, "tenant-a", "key", "hash", error)
    assert raised.value is error
    assert events == ["rollback", "lookup"]


@pytest.mark.parametrize("changed", [False, True])
def test_lost_message_insert_returns_exact_winner_or_409(monkeypatch, changed):
    db, tenant, account, _key = seed()
    db.add(Sender(tenant_id=tenant.id, sender="Telnexa", status="approved"))
    db.commit()
    body = SendRequest(
        billing_account_id=account.id,
        destination="+491234567",
        sender="Telnexa",
        content="original",
    )
    key = "concurrent-message-key"
    original = api.send(body, key, str(uuid.uuid4()), tenant.id, db)
    before = db.query(Wallet).one().reserved
    scalar = db.scalar
    hidden = False

    def stale_read(statement, *args, **kwargs):
        nonlocal hidden
        descriptions = getattr(statement, "column_descriptions", [])
        if not hidden and descriptions and descriptions[0].get("entity") is Message:
            hidden = True
            return None
        return scalar(statement, *args, **kwargs)

    def lose_insert(*args, **kwargs):
        raise IntegrityError("INSERT messages", {}, RuntimeError("unique tenant/key"))

    monkeypatch.setattr(db, "scalar", stale_read)
    monkeypatch.setattr(api, "accept_message", lose_insert)
    if changed:
        body = body.model_copy(update={"content": "different"})
        with pytest.raises(HTTPException) as error:
            api.send(body, key, str(uuid.uuid4()), tenant.id, db)
        assert error.value.status_code == 409
        assert error.value.detail == "idempotency_key_payload_mismatch"
    else:
        assert api.send(body, key, str(uuid.uuid4()), tenant.id, db) == original
    assert hidden
    assert db.query(Message).count() == 1
    assert db.query(SmsDispatchJob).count() == 1
    assert db.query(Wallet).one().reserved == before
