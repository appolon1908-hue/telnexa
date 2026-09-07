"""Real Middleware -> Telnexa HTTP read-back in a disposable PostgreSQL lab.

Two separate interpreters load each repository's real application dependencies.
No application-module shims, canned HTTP responses, provider workers or external
network destinations are used. This is source interoperability, not deployment.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MIDDLEWARE_SHA = "882f7378a27ca0a87c1cb496c5d24e73984caf65"
FLAGS = (
    "LIVE_SMS_DELIVERY",
    "LIVE_EMAIL_DELIVERY",
    "LIVE_PSTN_DIALING",
    "TELNEXA_PRODUCTION_SMS_ENABLED",
    "SMS_DELIVERY",
    "SMS_DELIVERY_ENABLED",
    "EXTERNAL_DELIVERY_ENABLED",
)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def guard_environment(env):
    url = urlsplit(env.get("BILLING_DATABASE_URL", ""))
    require(
        env.get("TELNEXA_DISPOSABLE_CERTIFICATION") == "true"
        and url.hostname == "127.0.0.1"
        and url.port == 5432
        and url.path == "/telnexa_issue29_ci"
        and url.username == "telnexa_ci"
        and url.scheme == "postgresql+psycopg"
        and not url.query
        and not url.fragment,
        "dedicated_loopback_disposable_database_required",
    )
    require(all(env.get(name) == "false" for name in FLAGS), "effect_flags_must_be_false")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", env.get("SOURCE_SHA", ""))), "exact_source_required")


def prohibit_external_sockets():
    """Deny Python socket egress except literal loopback; libpq is separately gated."""

    def allowed(address):
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}

    for method in ("connect", "connect_ex"):
        original = getattr(socket.socket, method)

        def checked(sock, address, original=original):
            require(allowed(address), "external_connection_forbidden")
            return original(sock, address)

        setattr(socket.socket, method, checked)


def revision(directory):
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def serve(fd):
    guard_environment(os.environ)
    prohibit_external_sockets()
    sys.path.insert(0, str(ROOT))
    import uvicorn

    uvicorn.run(
        "billing.app:app",
        fd=fd,
        loop="asyncio",
        http="h11",
        access_log=False,
        log_level="critical",
    )


def snapshot():
    from sqlalchemy import inspect
    from billing.db import SessionLocal
    from billing.models import Message, Outbox, SmsDispatchJob, Wallet
    from billing.sms_integration import SmsAcceptanceReceipt

    with SessionLocal() as db:
        # Authentication may update API-key last_used_at. It cannot modify any
        # message, financial, delivery, receipt or business-event state here.
        tables = (Message, SmsDispatchJob, SmsAcceptanceReceipt, Wallet, Outbox)
        state = {}
        for model in tables:
            rows = db.query(model).all()
            state[model.__tablename__] = sorted(
                [
                    {
                        column.name: str(
                            getattr(row, inspect(model).get_property_by_column(column).key)
                        )
                        for column in model.__table__.columns
                    }
                    for row in rows
                ],
                key=lambda row: json.dumps(row, sort_keys=True),
            )
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


async def consume(fixture, middleware_dir):
    """Use real validated Settings and command types in Middleware's interpreter."""
    require(revision(middleware_dir) == MIDDLEWARE_SHA, "middleware_source_mismatch")
    origin = urlsplit(fixture["origin"])
    require(
        origin.scheme == "http"
        and origin.hostname == "127.0.0.1"
        and origin.port is not None
        and not origin.path
        and not origin.username
        and not origin.query
        and not origin.fragment,
        "loopback_http_origin_required",
    )
    prohibit_external_sockets()
    sys.path.insert(0, str(middleware_dir))
    import httpx
    from app.config import Settings
    from app.temporal_workflows import CommandExecutionRequest
    from app.telnexa_provider_adapter import TelnexaProviderAdapterError, TelnexaSmsAdapter

    settings = Settings.from_env({"APP_ENV": "test", "ALLOW_IN_MEMORY_STORAGE": "true"})
    require(not settings.sms_delivery_enabled, "middleware_delivery_must_be_disabled")
    methods = []
    original_client = httpx.AsyncClient

    class ObservedClient(original_client):
        # Observe real HTTP without replacing any response or application module.
        async def request(self, method, url, *args, **kwargs):
            methods.append(method.upper())
            require(method.upper() == "GET", "reconciliation_attempted_a_write")
            require(str(url).startswith(fixture["origin"] + "/"), "unexpected_origin")
            return await super().request(method, url, *args, **kwargs)

    httpx.AsyncClient = ObservedClient
    env = {"TELNEXA_SMS_BASE_URL": fixture["origin"], "TELNEXA_SMS_API_KEY": fixture["api_key"]}
    fields = fixture["command"]
    adapter = TelnexaSmsAdapter(settings, env=env)
    cases = {}

    async def read(
        name, overrides=None, payload_changes=None, expected="mismatch", instance=adapter
    ):
        request_fields = dict(fields)
        request_fields.update(overrides or {})
        request_fields["payload"] = {**fields["payload"], **(payload_changes or {})}
        result = await instance.readback(CommandExecutionRequest(**request_fields))
        require(result.status == expected, name + "_failed")
        if expected == "matched":
            require(
                result.provider_operation_id == fixture["message_id"], "unstable_message_identity"
            )
        else:
            require(result.provider_operation_id is None, "fabricated_message_identity")
        cases[name] = "PASS"

    await read("real_delivered_readback", expected="matched")
    await read("missing_record_no_submission", {"idempotency_key": "never-submitted-cross-repo"})
    await read("changed_fingerprint_denied", payload_changes={"content": "different fixture"})
    await read("wrong_tenant_denied", {"tenant_id": str(uuid.uuid4())})
    await read("wrong_correlation_denied", {"correlation_id": str(uuid.uuid4())})
    wrong_key = TelnexaSmsAdapter(settings, env={**env, "TELNEXA_SMS_API_KEY": "wrong-fixture-key"})
    await read("wrong_key_denied", instance=wrong_key)
    before = len(methods)
    try:
        await adapter.execute(CommandExecutionRequest(**fields))
    except TelnexaProviderAdapterError:
        cases["delivery_disabled_before_http"] = "PASS"
    else:
        raise RuntimeError("disabled_delivery_was_accepted")
    require(len(methods) == before, "disabled_delivery_contacted_provider")
    require(methods == ["GET"] * 6, "unexpected_http_sequence")
    return {"cases": cases, "http_gets": len(methods), "http_posts": 0}


def orchestrate(middleware_dir, middleware_python):
    guard_environment(os.environ)
    require(revision(ROOT) == os.environ["SOURCE_SHA"], "telnexa_source_mismatch")
    require(revision(middleware_dir) == MIDDLEWARE_SHA, "middleware_source_mismatch")
    sys.path.insert(0, str(ROOT))
    from scripts.certify_private_sms_contract import main as certify_provider

    # Run the existing real PostgreSQL acceptance, concurrency, callback v2,
    # DLR, immutable replay and local STOP cases first. No provider is started.
    certify_provider()
    from sqlalchemy import select
    from billing.app import ph
    from billing.db import SessionLocal
    from billing.models import ApiKey, BillingAccount, Message, SmsDispatchJob

    with SessionLocal.begin() as db:
        message = db.scalar(select(Message).where(Message.idempotency_key == "same-command"))
        require(message is not None and message.status == "delivered", "certified_fixture_required")
        account = db.scalar(
            select(BillingAccount).where(BillingAccount.tenant_id == message.tenant_id)
        )
        raw_key = "tnx_" + uuid.uuid4().hex
        db.add(
            ApiKey(
                tenant_id=message.tenant_id,
                account_id=account.id,
                prefix=raw_key[:12],
                secret_hash=ph.hash(raw_key),
                scopes="sms.status.read",
            )
        )
        command = {
            "command_id": str(uuid.uuid4()),
            "command_type": "sms.message.submit.v1",
            "command_version": "1.0",
            "target": "telnexa-sms",
            "tenant_id": message.tenant_id,
            "requested_by": "issue29-disposable",
            "correlation_id": message.correlation_id,
            "idempotency_key": message.idempotency_key,
            "capability": "SMS_DELIVERY",
            "authenticated_client_id": "issue29-disposable",
            "payload": {
                "billing_account_id": account.id,
                "destination": message.destination,
                "sender": message.sender,
                "content": message.content,
                "category": "transactional",
                "campaign_id": None,
                "client_reference": None,
            },
        }
        message_id = message.id
    before = snapshot()
    import httpx

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve-fd", str(listener.fileno())],
            cwd=ROOT,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=1, trust_env=False) as client:
            while True:
                require(process.poll() is None, "lab_api_exited")
                try:
                    if client.get(origin + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                require(time.monotonic() < deadline, "lab_api_start_timeout")
                time.sleep(0.1)
        child_env = {
            key: value for key, value in os.environ.items() if not key.upper().endswith("_PROXY")
        }
        child_env["NO_PROXY"] = "127.0.0.1,localhost"
        result = subprocess.run(
            [
                str(middleware_python),
                str(Path(__file__).resolve()),
                "--consume",
                "--middleware-dir",
                str(middleware_dir),
            ],
            input=json.dumps(
                {"origin": origin, "api_key": raw_key, "command": command, "message_id": message_id}
            ),
            text=True,
            capture_output=True,
            timeout=90,
            cwd=middleware_dir,
            env=child_env,
        )
        # Never echo child output on failure: stdin carries a disposable API key.
        require(result.returncode == 0, "real_middleware_consumer_failed")
        outcome = json.loads(result.stdout)
        require(snapshot() == before, "readback_changed_business_state")
        with SessionLocal() as db:
            require(
                all(row.attempt_count == 0 for row in db.scalars(select(SmsDispatchJob))),
                "provider_effect_detected",
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    evidence = {
        "schema_version": "telnexa.issue29.cross-repository.v1",
        "telnexa_source_sha": os.environ["SOURCE_SHA"],
        "middleware_source_sha": MIDDLEWARE_SHA,
        "real_application_modules": True,
        "real_loopback_http": True,
        "disposable_postgresql": True,
        **outcome,
        "business_state_unchanged": True,
        "provider_submissions": 0,
        "staging_runtime_certified": False,
        "production_certified": False,
    }
    Path("issue29-cross-repository-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    print("CROSS_REPOSITORY_READBACK=PASS; PROVIDER_SUBMISSIONS=0; DEPLOYMENT_CERTIFIED=NO")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--middleware-dir", type=Path)
    parser.add_argument("--middleware-python", type=Path)
    parser.add_argument("--serve-fd", type=int)
    parser.add_argument("--consume", action="store_true")
    args = parser.parse_args()
    if args.serve_fd is not None:
        serve(args.serve_fd)
    elif args.consume:
        require(args.middleware_dir is not None, "middleware_directory_required")
        fixture = json.loads(sys.stdin.read(65537))
        print(json.dumps(asyncio.run(consume(fixture, args.middleware_dir.resolve()))))
    else:
        require(
            args.middleware_dir is not None and args.middleware_python is not None,
            "separate_interpreters_required",
        )
        # Keep the venv interpreter symlink; resolving it would bypass its site-packages.
        orchestrate(args.middleware_dir.resolve(), args.middleware_python.absolute())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Exception messages from network/drivers can include credentials/data.
        code = str(error) if re.fullmatch(r"[a-z_]{1,80}", str(error)) else type(error).__name__
        print("CROSS_REPOSITORY_CERTIFICATION_FAILED:" + code, file=sys.stderr)
        raise SystemExit(1) from None
