"""Private Telnexa provider API for governed Middleware SMS commands.

The service is intentionally separate from the public commercial API. It accepts
only the canonical ``sms.message.submit.v1`` command and defaults to a disabled
Jasmin transport. CI and isolated staging may opt into the internal no-effect
simulator. No live carrier transport is constructed by this module.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .sms_provider_runtime import (
    CallbackAuthenticationError,
    CallbackReplayConflict,
    DisabledJasminTransport,
    DlrCallback,
    HttpJasminSimulatorTransport,
    IdempotencyConflict,
    MoCallback,
    ProviderDisabled,
    ProviderNotFound,
    SmsCommand,
    SmsProviderError,
    SmsProviderService,
    create_session_factory,
    verify_callback,
)


def _boolean(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() == "true"


def _required_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError(f"{name} must contain at least 32 bytes")
    return value


def build_provider_service() -> SmsProviderService:
    database_url = os.environ.get(
        "TELNEXA_SMS_DATABASE_URL",
        "sqlite:////tmp/telnexa-sms-provider.db",
    )
    _, session_factory = create_session_factory(
        database_url,
        create_schema=_boolean("TELNEXA_SMS_AUTO_CREATE_SCHEMA", True),
    )
    mode = os.environ.get("TELNEXA_JASMIN_TRANSPORT", "disabled")
    if mode == "disabled":
        transport = DisabledJasminTransport()
    elif mode == "http-simulator":
        transport = HttpJasminSimulatorTransport(
            os.environ.get("TELNEXA_JASMIN_SIMULATOR_URL", "http://jasmin-simulator:8080")
        )
    else:
        raise RuntimeError("TELNEXA_JASMIN_TRANSPORT must be disabled or http-simulator")
    return SmsProviderService(session_factory=session_factory, transport=transport)


def create_app(service: SmsProviderService | None = None) -> FastAPI:
    provider = service or build_provider_service()
    app = FastAPI(
        title="Telnexa SMS Provider Runtime",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/provider/openapi.json",
    )
    app.state.provider = provider

    @app.exception_handler(SmsProviderError)
    async def provider_error_handler(
        request: Request,
        error: SmsProviderError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.code, "detail": str(error)},
        )

    def middleware_identity(
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    ) -> str:
        expected = _required_secret("TELNEXA_MIDDLEWARE_BEARER_TOKEN")
        supplied = (
            authorization.removeprefix("Bearer ")
            if authorization and authorization.startswith("Bearer ")
            else ""
        )
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="middleware_authentication_required")
        if not x_tenant_id:
            raise HTTPException(status_code=400, detail="tenant_header_required")
        return x_tenant_id

    @app.get("/health")
    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return provider.health()

    @app.get("/ready")
    @app.get("/readyz")
    def ready() -> dict[str, Any]:
        health_state = provider.health()
        return {
            "status": "ready" if health_state["database"] == "ok" else "not_ready",
            **health_state,
        }

    @app.post("/api/v1/provider/operations", status_code=202)
    @app.post("/api/v1/commands/sms.message.submit.v1", status_code=202)
    def submit(
        command: SmsCommand,
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        if command.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="tenant_mismatch")
        return provider.submit(command)

    @app.get("/api/v1/provider/operations/{operation_id}")
    @app.get("/api/v1/messages/{operation_id}")
    def get_operation(
        operation_id: str,
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return provider.get(tenant_id, operation_id)

    @app.get("/api/v1/messages/by-middleware/{message_id}")
    def get_by_message(
        message_id: str,
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return provider.get_by_message(tenant_id, message_id)

    @app.post("/api/v1/provider/operations/{operation_id}/reconcile")
    def reconcile(
        operation_id: str,
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return provider.reconcile(tenant_id, operation_id)

    async def authenticated_callback(
        request: Request,
    ) -> tuple[bytes, str, str]:
        raw_body = await request.body()
        timestamp = request.headers.get("X-Telnexa-Timestamp", "")
        event_id = request.headers.get("X-Telnexa-Event-Id", "")
        signature = request.headers.get("X-Telnexa-Signature", "")
        digest = verify_callback(
            secret=_required_secret("TELNEXA_PROVIDER_CALLBACK_SECRET"),
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
            signature=signature,
        )
        return raw_body, event_id, digest

    @app.post("/api/v1/provider/callbacks/dlr", status_code=202)
    async def dlr(request: Request) -> dict[str, Any]:
        raw_body, event_id, digest = await authenticated_callback(request)
        try:
            callback = DlrCallback.model_validate_json(raw_body)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid_dlr_payload") from error
        return provider.record_dlr(callback, event_id=event_id, payload_digest=digest)

    @app.post("/api/v1/provider/callbacks/mo", status_code=202)
    async def mo(request: Request) -> dict[str, Any]:
        raw_body, event_id, digest = await authenticated_callback(request)
        try:
            callback = MoCallback.model_validate_json(raw_body)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid_mo_payload") from error
        return provider.record_mo(callback, event_id=event_id, payload_digest=digest)

    @app.get("/api/v1/provider/usage")
    def usage(
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return provider.usage(tenant_id)

    @app.get("/api/v1/provider/opt-outs")
    def opt_outs(
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return {"items": provider.opt_outs(tenant_id)}

    @app.get("/api/v1/provider/callbacks")
    def callbacks(
        tenant_id: str = Depends(middleware_identity),
    ) -> dict[str, Any]:
        return {"items": provider.callbacks(tenant_id)}

    return app


app = create_app()


__all__ = [
    "app",
    "build_provider_service",
    "create_app",
    "CallbackAuthenticationError",
    "CallbackReplayConflict",
    "IdempotencyConflict",
    "ProviderDisabled",
    "ProviderNotFound",
]
