import os
import socket
import time

from .adapters import JasminHttpAdapter
from .db import SessionLocal
from .dispatch import claim_job, process_job
from .production_gates import production_enabled


def adapter_for(provider):
    prefix = provider.credential_reference or "/run/secrets/jasmin"
    return JasminHttpAdapter(
        provider.base_url or "http://jasmin:1401",
        prefix + "_username",
        prefix + "_password",
        os.environ.get("TELNEXA_DLR_RELAY_URL", "http://webhook-relay:8080"),
        os.environ.get("TELNEXA_DLR_SOURCE_KEY_ID", "jasmin-primary"),
        prefix + "_dlr_token",
        provider.connect_timeout_ms,
        provider.request_timeout_ms,
    )


def run_once(owner=None):
    if not production_enabled():
        return False
    with SessionLocal.begin() as db:
        job = claim_job(db, owner or f"{socket.gethostname()}:{os.getpid()}")
        if not job:
            return False
        process_job(db, job, adapter_for)
    return True


def main():
    while True:
        if not run_once():
            time.sleep(2)


if __name__ == "__main__":
    main()
