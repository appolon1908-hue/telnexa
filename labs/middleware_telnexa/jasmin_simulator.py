"""No-effect Jasmin simulator for isolated provider certification.

The simulator never opens SMPP connections and never contacts a carrier. It can
persist a synthetic acceptance before returning an HTTP 503 so the provider's
unknown-outcome reconciliation path can be proven without sending an SMS.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    channel: Literal["sms"]
    destination: str
    sender: str
    content: str
    encoding: Literal["GSM-7", "UCS-2"]
    characters: int = Field(ge=1)
    segments: int = Field(ge=1)
    category: str
    client_reference: str
    scheduled_at: str | None = None
    billing_account_id: str | None = None
    campaign_id: str | None = None


class ResetRequest(BaseModel):
    scenario: Literal["accepted", "rejected", "ambiguous_after_acceptance"] = (
        "ambiguous_after_acceptance"
    )
    readback_failures: int = Field(default=2, ge=0, le=10)


@dataclass
class State:
    scenario: str = "ambiguous_after_acceptance"
    readback_failures: int = 2
    submit_calls: int = 0
    readback_calls: int = 0
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


state = State()
app = FastAPI(title="No-effect Jasmin Simulator", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "carrierConnections": 0, "smsSent": 0, "noEffect": True}


@app.post("/control/reset")
def reset(request: ResetRequest) -> dict[str, Any]:
    with state.lock:
        state.scenario = request.scenario
        state.readback_failures = request.readback_failures
        state.submit_calls = 0
        state.readback_calls = 0
        state.messages.clear()
    return {"status": "reset", "noEffect": True}


@app.post("/submit", status_code=202)
def submit(request: SubmitRequest) -> dict[str, Any]:
    with state.lock:
        state.submit_calls += 1
        prior = state.messages.get(request.client_reference)
        if prior is None:
            prior = {
                "provider_reference": "jasmin-sim-" + str(uuid.uuid4()),
                "status": "accepted",
                "client_reference": request.client_reference,
                "message_id": request.message_id,
                "content_sha256_only": True,
            }
            state.messages[request.client_reference] = prior
        if state.scenario == "rejected":
            raise HTTPException(status_code=422, detail="synthetic_rejection")
        if state.scenario == "ambiguous_after_acceptance":
            raise HTTPException(status_code=503, detail="synthetic_timeout_after_acceptance")
        return prior


@app.get("/messages/{client_reference}")
def readback(client_reference: str) -> dict[str, Any]:
    with state.lock:
        state.readback_calls += 1
        if state.readback_calls <= state.readback_failures:
            raise HTTPException(status_code=503, detail="synthetic_readback_unavailable")
        row = state.messages.get(client_reference)
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        return dict(row)


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    with state.lock:
        return {
            "submit_calls": state.submit_calls,
            "readback_calls": state.readback_calls,
            "messages": len(state.messages),
            "carrier_connections": 0,
            "sms_sent": 0,
            "no_effect": True,
        }
