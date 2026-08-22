import hashlib
import importlib.util
import os
import json
import tempfile
from pathlib import Path
import unittest

os.environ.setdefault("WEBHOOK_HMAC_SECRET", "test-only-secret")
spec = importlib.util.spec_from_file_location(
    "relay", Path(__file__).parents[1] / "docker/webhook-relay/server.py"
)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class SignatureTest(unittest.TestCase):
    def test_signature_covers_timestamp_separator_and_exact_body(self):
        secret = b"shared-secret"
        timestamp = "1786766400"
        body = b'{"event":"inbound"}'
        value = relay.make_signature(
            secret, "POST", "/webhooks/sms/dlr", timestamp, "event-1", body
        )
        self.assertEqual(
            value,
            relay.make_signature(
                secret, "POST", "/webhooks/sms/dlr/", timestamp, "event-1", body
            ),
        )
        for changed in (
            ("PUT", "/webhooks/sms/dlr", timestamp, "event-1", body),
            ("POST", "/webhooks/sms/failed", timestamp, "event-1", body),
            ("POST", "/webhooks/sms/dlr", timestamp, "event-2", body),
            ("POST", "/webhooks/sms/dlr", timestamp, "event-1", body + b" "),
        ):
            self.assertNotEqual(relay.make_signature(secret, *changed), value)

    def test_jasmin_query_credential_is_verified_and_removed(self):
        token = "synthetic-provider-token"
        with tempfile.NamedTemporaryFile(mode="w") as registry:
            json.dump(
                {
                    "keys": [
                        {
                            "id": "jasmin",
                            "enabled": True,
                            "sha256": hashlib.sha256(token.encode()).hexdigest(),
                        }
                    ]
                },
                registry,
            )
            registry.flush()
            os.environ["TELNEXA_PROVIDER_KEYS_FILE"] = registry.name
            values = {
                "source_key_id": "jasmin",
                "source_token": token,
                "id": "message-1",
            }
            self.assertTrue(relay.authenticated_source({}, values))
            self.assertEqual(values, {"id": "message-1"})
            self.assertFalse(
                relay.authenticated_source(
                    {}, {"source_key_id": "jasmin", "source_token": "wrong"}
                )
            )


if __name__ == "__main__":
    unittest.main()
