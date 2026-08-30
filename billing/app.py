"""Composed Telnexa API entrypoint.

The commercial API remains isolated in ``billing.commercial_app`` while this
module registers the durable SMS provider models before schema creation and
mounts the provider-neutral Telnexa/Jasmin runtime.
"""

from .sms_provider import models as _sms_provider_models  # noqa: F401
from .commercial_app import app, ph
from .sms_provider.api import router as sms_provider_router

app.include_router(sms_provider_router)
app.state.sms_provider_runtime = "v1"

__all__ = ["app", "ph"]
