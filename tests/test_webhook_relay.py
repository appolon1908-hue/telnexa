import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("WEBHOOK_HMAC_SECRET", "test-only-secret")
os.environ.setdefault("TELNEXA_ENV", "development")
spec = importlib.util.spec_from_file_location(
    "relay", Path(__file__).parents[1] / "docker/webhook-relay/server.py"
)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = json.dumps(body).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.body


class SignatureTest(unittest.TestCase):
    def setUp(self):
        relay._token_cache.clear()

    def test_signature_covers_source_timestamp_path_event_and_exact_body(self):
        secret = b"shared-secret"
        timestamp = "1786766400"
        body = b'{"event":"inbound"}'
        value = relay.make_signature(
            secret,
            "POST",
            "/api/v1/telnexa/events",
            timestamp,
            "event-1",
            body,
        )
        self.assertEqual(
            value,
            relay.make_signature(
                secret,
                "POST",
                "/api/v1/telnexa/events/",
                timestamp,
                "event-1",
                body,
            ),
        )
        for changed in (
            ("PUT", "/api/v1/telnexa/events", timestamp, "event-1", body),
            ("POST", "/api/v1/telnexa/other", timestamp, "event-1", body),
            ("POST", "/api/v1/telnexa/events", timestamp, "event-2", body),
            ("POST", "/api/v1/telnexa/events", timestamp, "event-1", body + b" "),
        ):
            self.assertNotEqual(relay.make_signature(secret, *changed), value)

    def test_jasmin_query_credential_is_verified_removed_and_tenant_bound(self):
        token = "synthetic-provider-token"
        with tempfile.NamedTemporaryFile(mode="w") as registry:
            json.dump(
                {
                    "keys": [
                        {
                            "id": "jasmin",
                            "enabled": True,
                            "tenant_id": "tenant-1",
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
            principal = relay.authenticated_source({}, values)
            self.assertEqual(principal["tenant_id"], "tenant-1")
            self.assertEqual(values, {"id": "message-1"})
            self.assertIsNone(
                relay.authenticated_source({}, {"source_key_id": "jasmin", "source_token": "wrong"})
            )

    def test_event_id_is_stable_and_envelope_matches_middleware_schema(self):
        values = {"message_id": "message-1", "status": "DELIVRD"}
        first = relay.stable_event_id("dlr", values)
        second = relay.stable_event_id("dlr", dict(values))
        self.assertEqual(first, second)
        envelope = relay.build_envelope(
            "dlr",
            first,
            {"tenant_id": "tenant-1"},
            values,
            "2026-08-26T20:00:00+00:00",
        )
        self.assertEqual(envelope["specversion"], "1.0")
        self.assertEqual(envelope["type"], "codestra.sms.message.delivered")
        self.assertEqual(envelope["source"], "urn:codestra:telnexa-gateway")
        self.assertEqual(envelope["tenant_id"], "tenant-1")
        self.assertEqual(envelope["idempotency_key"], first)
        self.assertEqual(envelope["actor"], {"type": "service", "id": "telnexa-gateway"})
        for field in (
            "id",
            "subject",
            "time",
            "correlation_id",
            "causation_id",
            "schema_version",
            "data",
        ):
            self.assertIn(field, envelope)

    def test_client_credentials_token_is_short_lived_scoped_and_cached(self):
        with tempfile.NamedTemporaryFile(mode="w") as secret_file:
            secret_file.write("client-secret")
            secret_file.flush()
            os.environ["TELNEXA_GATEWAY_CLIENT_SECRET_FILE"] = secret_file.name
            os.environ["KEYCLOAK_TOKEN_ENDPOINT"] = relay.CANONICAL_TOKEN_ENDPOINT
            os.environ["TELNEXA_GATEWAY_CLIENT_ID"] = "telnexa-gateway"
            calls = []

            def fake_urlopen(request, timeout):
                calls.append(request)
                return FakeResponse(
                    {
                        "access_token": "machine-token",
                        "token_type": "Bearer",
                        "expires_in": 300,
                    }
                )

            with patch.object(relay.urllib.request, "urlopen", fake_urlopen):
                first = relay.access_token("sms.events.publish")
                second = relay.access_token("sms.events.publish")

            self.assertEqual(first, "machine-token")
            self.assertEqual(second, "machine-token")
            self.assertEqual(len(calls), 1)
            encoded = calls[0].data.decode()
            self.assertIn("grant_type=client_credentials", encoded)
            self.assertIn("client_id=telnexa-gateway", encoded)
            self.assertIn("scope=sms.events.publish", encoded)

    def test_refresh_token_or_long_lifetime_is_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w") as secret_file:
            secret_file.write("client-secret")
            secret_file.flush()
            os.environ["TELNEXA_GATEWAY_CLIENT_SECRET_FILE"] = secret_file.name
            os.environ["KEYCLOAK_TOKEN_ENDPOINT"] = relay.CANONICAL_TOKEN_ENDPOINT
            os.environ["TELNEXA_GATEWAY_CLIENT_ID"] = "telnexa-gateway"
            for body in (
                {"access_token": "token", "expires_in": 301},
                {"access_token": "token", "expires_in": 300, "refresh_token": "forbidden"},
            ):
                relay._token_cache.clear()
                with patch.object(
                    relay.urllib.request,
                    "urlopen",
                    return_value=FakeResponse(body),
                ):
                    with self.assertRaises(RuntimeError):
                        relay.access_token("sms.events.publish")


if __name__ == "__main__":
    unittest.main()
