import time
from sqlalchemy import select
from .db import SessionLocal
from .models import SmsProviderEventInbox
from .provider_events import process_event


def run_once():
    with SessionLocal.begin() as db:
        query = (
            select(SmsProviderEventInbox)
            .where(SmsProviderEventInbox.state == "pending")
            .order_by(SmsProviderEventInbox.received_at)
            .limit(1)
        )
        if db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = db.scalar(query)
        if not row:
            return False
        process_event(db, row)
    return True


def main():
    while True:
        if not run_once():
            time.sleep(2)


if __name__ == "__main__":
    main()
