"""Serialize commercial message acceptance and reconcile a lost insert race."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select, text

from .models import Message


def lock_message_key(db, tenant_id: str, key: str) -> None:
    """Hold a cross-process PostgreSQL key lock until commit or rollback.

    Lock only authenticated tenant/key pairs, before reserving canary or billing
    capacity. A digest collision can only serialize unrelated keys, not grant
    access or combine their database identities. SQLite is for offline tests.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    identity = json.dumps([str(tenant_id), key], separators=(",", ":")).encode()
    lock_id = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


def recover_message_insert_race(db, tenant_id, key, request_hash, error):
    """Rollback all losing reservations, then read the committed winning message.

    Do not reinterpret unrelated integrity failures as duplicates and never
    submit again. PostgreSQL's uniqueness check waits for the winning transaction
    before reporting its conflict; the next READ COMMITTED lookup sees its row.
    """
    db.rollback()
    winner = db.scalar(
        select(Message).where(Message.tenant_id == tenant_id, Message.idempotency_key == key)
    )
    if winner is None:
        raise error
    if winner.request_hash != request_hash:
        raise HTTPException(409, "idempotency_key_payload_mismatch") from error
    return winner
