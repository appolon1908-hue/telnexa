"""Public facade for the durable, fail-closed Telnexa SMS provider runtime."""

from dataclasses import dataclass

from .sms_provider_contracts import *  # noqa: F403
from .sms_provider_contracts import __all__ as _contracts_all
from .sms_provider_events import SmsProviderEventsMixin
from .sms_provider_models import *  # noqa: F403
from .sms_provider_models import __all__ as _models_all
from .sms_provider_service_base import SmsProviderServiceBase
from .sms_provider_submission import SmsProviderSubmissionMixin
from .sms_provider_transport import *  # noqa: F403
from .sms_provider_transport import __all__ as _transport_all


@dataclass(slots=True)
class SmsProviderService(
    SmsProviderSubmissionMixin,
    SmsProviderEventsMixin,
    SmsProviderServiceBase,
):
    """Single provider service assembled from small, reviewable mixins."""


__all__ = [*_contracts_all, *_models_all, *_transport_all, "SmsProviderService"]
