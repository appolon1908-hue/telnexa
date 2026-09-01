"""Disposable-PostgreSQL certification for shared provider capacity."""

import threading
import time

from billing.db import Base, SessionLocal, engine
from billing.models import Provider
from billing.provider_capacity import acquire_provider_capacity, release_provider_capacity


def create_provider(name, max_inflight, tps):
    with SessionLocal.begin() as db:
        provider = Provider(
            name=name,
            connector=name,
            state="enabled",
            routing_enabled=True,
            adapter_type="jasmin_http",
            max_inflight=max_inflight,
            tps=tps,
        )
        db.add(provider)
        db.flush()
        return provider.id


def concurrent_round(provider_id, workers=20, hold=0):
    barrier = threading.Barrier(workers)
    observation_lock = threading.Lock()
    observed = {"active": 0, "maximum": 0, "successes": 0}

    def worker():
        with SessionLocal.begin() as db:
            barrier.wait()
            if not acquire_provider_capacity(db, provider_id):
                return
            with observation_lock:
                observed["active"] += 1
                observed["successes"] += 1
                observed["maximum"] = max(observed["maximum"], observed["active"])
            time.sleep(hold)
            with observation_lock:
                observed["active"] -= 1
            release_provider_capacity(db, provider_id)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return observed


if __name__ == "__main__":
    if engine.dialect.name != "postgresql":
        raise SystemExit("PostgreSQL is required")
    Base.metadata.create_all(engine)
    inflight = concurrent_round(create_provider("capacity-max-inflight", 1, 100), hold=0.02)
    tps = concurrent_round(create_provider("capacity-tps", 20, 3))
    assert inflight["maximum"] <= 1, inflight
    assert tps["successes"] == 3, tps
    print({"max_inflight": inflight, "tps": tps})
