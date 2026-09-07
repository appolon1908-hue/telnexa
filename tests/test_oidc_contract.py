"""Offline, real-signature regressions for separate portal and machine trusts."""

import base64
import json
import time
from io import BytesIO

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from billing import codestra_identity as machine
from billing import oidc


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="test-signing-key", alg="RS256", use="sig")
    return private, jwk


@pytest.fixture
def identity(monkeypatch, keypair):
    private, jwk = keypair
    monkeypatch.setenv("CODESTRA_OIDC_ENABLED", "true")
    monkeypatch.setenv("CODESTRA_OIDC_ALLOWED_AZP", "middleware-worker")
    monkeypatch.setenv("OIDC_ISSUER", "https://api.telnexa.co/auth/realms/telnexa")
    monkeypatch.setenv("OIDC_AUDIENCE", "telnexa-api")
    monkeypatch.setenv("OIDC_ALLOWED_AZP", "telnexa-portal")
    monkeypatch.setattr(machine, "_cache", {"at": time.monotonic(), "keys": {jwk["kid"]: jwk}})
    monkeypatch.setattr(oidc, "_jwks", lambda: {jwk["kid"]: jwk})
    now = int(time.time())
    claims = {
        "iss": machine.ISSUER,
        "aud": machine.AUDIENCE,
        "sub": "service-account-middleware-worker",
        "azp": "middleware-worker",
        "iat": now - 1,
        "exp": now + 299,
        "jti": "unique-test-token",
        "tenant_id": "tenant-a",
        "account_id": "account-a",
        "scope": "sms.send sms.status.read",
    }

    def signed(changes=None, remove=None):
        body = dict(claims)
        body.update(changes or {})
        for name in remove or []:
            body.pop(name, None)
        return jwt.encode(body, private, algorithm="RS256", headers={"kid": jwk["kid"]})

    return signed


def authenticate(token, tenant="tenant-a", scope="sms.send"):
    return oidc.validate_bearer("Bearer " + token, tenant, scope)


def test_canonical_machine_identity_and_readback(identity):
    result = authenticate(identity())
    assert result["tenant_id"] == "tenant-a"
    assert result["account_id"] == "account-a"
    assert result["roles"] == []
    assert authenticate(identity(), scope="sms.status.read") == result
    assert authenticate(identity(), scope="messages:write") == result


def test_machine_trust_is_disabled_by_default(identity, monkeypatch):
    monkeypatch.delenv("CODESTRA_OIDC_ENABLED")
    with pytest.raises(HTTPException) as error:
        authenticate(identity())
    assert error.value.status_code == 403
    assert error.value.detail == "codestra_identity_disabled"


def test_empty_machine_client_allowlist_fails_closed(identity, monkeypatch):
    monkeypatch.setenv("CODESTRA_OIDC_ALLOWED_AZP", " , ")
    with pytest.raises(HTTPException) as error:
        authenticate(identity())
    assert error.value.status_code == 503


@pytest.mark.parametrize("name", ["iss", "sub", "aud", "azp", "iat", "exp", "jti"])
def test_missing_mandatory_claim_is_rejected(identity, name):
    with pytest.raises(HTTPException) as error:
        authenticate(identity(remove=[name]))
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "codestra-api"},
        {"aud": [machine.AUDIENCE, "other-api"]},
        {"iss": "https://attacker.invalid/realm"},
        {"exp": int(time.time()) + 600},
        {"exp": int(time.time()) - 10},
        {"iat": int(time.time()) + 60},
        {"iat": True},
        {"iat": str(int(time.time()) - 1)},
        {"jti": ""},
        {"sub": ""},
        {"account_id": ""},
        {"tenant_id": []},
        {"scope": ["sms.send"]},
    ],
)
def test_invalid_machine_claims_are_rejected(identity, changes):
    with pytest.raises(HTTPException) as error:
        authenticate(identity(changes))
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "changes, tenant, scope",
    [
        ({"azp": "telnexa-portal"}, "tenant-a", "sms.send"),
        ({}, "tenant-b", "sms.send"),
        ({"scope": "sms.send"}, "tenant-a", "sms.status.read"),
        ({"scope": "sms.status.read"}, "tenant-a", "sms.send"),
        ({"scope": "sms.read"}, "tenant-a", "read"),
        ({"scope": "*", "realm_access": {"roles": ["ADMIN"]}}, "tenant-a", "sms.send"),
        (
            {"scope": "", "resource_access": {"telnexa-gateway": {"roles": ["OWNER"]}}},
            "tenant-a",
            "sms.send",
        ),
        ({}, "tenant-a", "senders:write"),
    ],
)
def test_machine_roles_and_cross_tenant_access_do_not_grant_scope(identity, changes, tenant, scope):
    with pytest.raises(HTTPException) as error:
        authenticate(identity(changes), tenant=tenant, scope=scope)
    assert error.value.status_code == 403


def test_local_portal_remains_usable_when_machine_trust_disabled(identity, monkeypatch):
    monkeypatch.delenv("CODESTRA_OIDC_ENABLED")
    token = identity(
        {
            "iss": "https://api.telnexa.co/auth/realms/telnexa",
            "aud": "telnexa-api",
            "azp": "telnexa-portal",
            "scope": "sms.read",
            "realm_access": {"roles": ["READ_ONLY"]},
        }
    )
    assert authenticate(token, scope="read")["roles"] == ["READ_ONLY"]


def test_unsigned_and_wrongly_signed_tokens_are_rejected(identity):
    token = identity()
    header, payload, signature = token.split(".")
    wrong = "A" if signature[0] != "A" else "B"
    with pytest.raises(HTTPException) as error:
        authenticate(".".join([header, payload, wrong + signature[1:]]))
    assert error.value.status_code == 401
    with pytest.raises(HTTPException):
        authenticate(jwt.encode({"iss": machine.ISSUER}, key="", algorithm="none"))


@pytest.mark.parametrize("token", ["", "invalid", "a.!!.b", "a.bnVsbA.b", "x" * 16385])
def test_invalid_routing_hints_cannot_authenticate(token):
    with pytest.raises(HTTPException) as error:
        authenticate(token)
    assert error.value.status_code == 401


def test_unknown_signing_key_refresh_is_bounded(identity, keypair, monkeypatch):
    _, jwk = keypair
    calls = []
    monkeypatch.setattr(machine, "_load_keys", lambda: calls.append(1) or {jwk["kid"]: jwk})
    for _ in range(3):
        with pytest.raises(HTTPException):
            machine._signing_key("unknown-kid")
    assert calls == []
    machine._cache["at"] -= 31
    with pytest.raises(HTTPException):
        machine._signing_key("unknown-kid")
    assert calls == [1]


def test_jwks_rejects_duplicate_key_ids(keypair, monkeypatch):
    _, jwk = keypair

    class Opener:
        def open(self, url, timeout):
            assert url == machine.JWKS_URL
            return BytesIO(json.dumps({"keys": [jwk, jwk]}).encode())

    monkeypatch.setattr(machine.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(HTTPException) as error:
        machine._load_keys()
    assert error.value.status_code == 503


def test_no_implicit_authentication_without_bearer():
    assert oidc.validate_bearer(None, "tenant-a") is None


def test_jwks_redirects_are_denied():
    request = machine.urllib.request.Request(machine.JWKS_URL)
    with pytest.raises(machine.urllib.error.HTTPError):
        machine._NoRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://attacker.invalid/keys"
        )


def test_expired_key_cache_does_not_fail_open(identity, monkeypatch):
    machine._cache["at"] -= 301

    def unavailable():
        raise HTTPException(503, "codestra_identity_keys_unavailable")

    monkeypatch.setattr(machine, "_load_keys", unavailable)
    with pytest.raises(HTTPException) as error:
        authenticate(identity())
    assert error.value.status_code == 503


@pytest.mark.parametrize("required", ["read", "sms.read"])
@pytest.mark.parametrize("scopes", ["sms.status.read", "read sms.read sms.status.read *"])
def test_status_token_cannot_satisfy_generic_tenant_reads(identity, required, scopes):
    with pytest.raises(HTTPException) as error:
        authenticate(identity({"scope": scopes}), scope=required)
    assert error.value.status_code == 403
    assert error.value.detail == "explicit_machine_read_scope_required"


def test_deeply_nested_untrusted_payload_returns_401():
    payload = base64.urlsafe_b64encode(("[" * 2000 + "]" * 2000).encode()).decode().rstrip("=")
    token = "e30." + payload + ".signature"
    assert len(token) < machine.MAX_TOKEN_BYTES
    with pytest.raises(HTTPException) as error:
        authenticate(token)
    assert error.value.status_code == 401
    assert error.value.detail == "invalid_access_token"
