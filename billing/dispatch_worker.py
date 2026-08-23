import os
import socket
import time

from .adapters import JasminHttpAdapter
from .adapters.errors import AdapterConfigurationError
from .db import SessionLocal
from .dispatch import claim_job, process_job
from .production_gates import production_enabled


def adapter_for(provider):
    prefix = provider.credential_reference
    secret_root = os.environ.get("TELNEXA_PROVIDER_SECRET_ROOT", "/run/secrets").rstrip("/")
    if not prefix or not prefix.startswith(secret_root + "/") or ".." in prefix:
        raise AdapterConfigurationError("explicit_provider_credential_reference_required")
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


def validate_provider_credentials(db):
    from sqlalchemy import select
    from .models import Provider

    providers = db.scalars(
        select(Provider).where(
            Provider.state == "enabled",
            Provider.routing_enabled == True,
            Provider.adapter_type == "jasmin_http",
        )
    ).all()
    if not providers:
        raise AdapterConfigurationError("no_enabled_jasmin_provider")
    return {provider.id: adapter_for(provider).validate_credentials() for provider in providers}


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
    if production_enabled():
        with SessionLocal() as db:
            validate_provider_credentials(db)
    while True:
        if not run_once():
            time.sleep(2)


if __name__ == "__main__":
    main()
