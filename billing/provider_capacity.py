from datetime import datetime, timedelta, timezone

from sqlalchemy import case, update

from .models import Provider


def acquire_provider_capacity(db, provider_id, now=None):
    """Atomically acquire shared provider inflight and one-second TPS capacity."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=1)
    db.execute(
        update(Provider)
        .where(
            Provider.id == provider_id,
            (Provider.tps_window_started_at == None) | (Provider.tps_window_started_at <= cutoff),
        )
        .values(tps_window_started_at=now, tps_window_count=0)
    )
    acquired = db.execute(
        update(Provider)
        .where(
            Provider.id == provider_id,
            Provider.inflight_count < Provider.max_inflight,
            Provider.tps_window_count < Provider.tps,
        )
        .values(
            inflight_count=Provider.inflight_count + 1,
            tps_window_count=Provider.tps_window_count + 1,
        )
        .returning(Provider.id)
    ).first()
    return acquired is not None


def release_provider_capacity(db, provider_id):
    db.execute(
        update(Provider)
        .where(Provider.id == provider_id)
        .values(
            inflight_count=case((Provider.inflight_count > 0, Provider.inflight_count - 1), else_=0)
        )
    )
