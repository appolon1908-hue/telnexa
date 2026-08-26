import os
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from billing import oidc


class OidcContractTest(unittest.TestCase):
    def setUp(self):
        os.environ["OIDC_ISSUER"] = oidc.CANONICAL_ISSUER
        os.environ["OIDC_AUDIENCE"] = oidc.CANONICAL_AUDIENCE
        os.environ["OIDC_ALLOWED_AZP"] = "middleware-api,monitoring-readonly"

    def test_canonical_machine_audience_and_lifetime(self):
        self.assertEqual(oidc.CANONICAL_AUDIENCE, "telnexa-gateway")
        now = int(time.time())
        oidc._validate_machine_token_lifetime({"iat": now, "exp": now + 300})
        with self.assertRaises(HTTPException) as error:
            oidc._validate_machine_token_lifetime({"iat": now, "exp": now + 301})
        self.assertEqual(error.exception.detail, "machine_token_lifetime_invalid")

    def test_canonical_configuration_is_fail_closed(self):
        os.environ["OIDC_AUDIENCE"] = "codestra-api"
        with self.assertRaises(HTTPException) as error:
            oidc.validate_bearer("Bearer token", "tenant-1", "sms.send")
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.detail, "canonical_identity_unavailable")

    def test_allowed_client_tenant_audience_and_scope_are_returned(self):
        now = int(time.time())
        claims = {
            "iss": oidc.CANONICAL_ISSUER,
            "sub": "service-account-middleware-api",
            "aud": ["telnexa-gateway"],
            "azp": "middleware-api",
            "iat": now,
            "exp": now + 300,
            "jti": "token-1",
            "tenant_id": "tenant-1",
            "account_id": "account-1",
            "scope": "sms.send sms.status.read",
            "realm_access": {"roles": []},
        }
        fake_jwk = type("FakeJwk", (), {"key": object()})()
        with (
            patch.object(
                oidc.jwt, "get_unverified_header", return_value={"alg": "RS256", "kid": "key-1"}
            ),
            patch.object(oidc, "_jwks", return_value={"key-1": {"kid": "key-1"}}),
            patch.object(oidc.jwt.PyJWK, "from_dict", return_value=fake_jwk),
            patch.object(oidc.jwt, "decode", return_value=claims),
        ):
            result = oidc.validate_bearer("Bearer token", "tenant-1", "sms.status.read")
        self.assertEqual(result["authorized_party"], "middleware-api")
        self.assertEqual(result["audience"], "telnexa-gateway")
        self.assertEqual(result["token_id"], "token-1")

    def test_wrong_authorized_party_is_denied(self):
        now = int(time.time())
        claims = {
            "iss": oidc.CANONICAL_ISSUER,
            "sub": "service-account-unrelated",
            "aud": ["telnexa-gateway"],
            "azp": "unrelated-client",
            "iat": now,
            "exp": now + 300,
            "jti": "token-2",
            "tenant_id": "tenant-1",
            "account_id": "account-1",
            "scope": "sms.send",
            "realm_access": {"roles": []},
        }
        fake_jwk = type("FakeJwk", (), {"key": object()})()
        with (
            patch.object(
                oidc.jwt, "get_unverified_header", return_value={"alg": "RS256", "kid": "key-1"}
            ),
            patch.object(oidc, "_jwks", return_value={"key-1": {"kid": "key-1"}}),
            patch.object(oidc.jwt.PyJWK, "from_dict", return_value=fake_jwk),
            patch.object(oidc.jwt, "decode", return_value=claims),
        ):
            with self.assertRaises(HTTPException) as error:
                oidc.validate_bearer("Bearer token", "tenant-1", "sms.send")
        self.assertEqual(error.exception.detail, "client_identity_denied")


if __name__ == "__main__":
    unittest.main()
