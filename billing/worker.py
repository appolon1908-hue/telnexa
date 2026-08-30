"""Composed billing and SMS-provider worker entrypoint."""

import time

from .commercial_worker import deliver_webhooks
from .commercial_worker import once as commercial_once
from .sms_provider.worker import once as sms_provider_once


def once(sender=None):
    return commercial_once(sender) + sms_provider_once()


if __name__ == "__main__":
    while True:
        once()
        time.sleep(5)


__all__ = ["deliver_webhooks", "once"]
