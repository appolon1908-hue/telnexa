"""Opt-in Codestra machine identity; never replace the local portal authority."""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

import jwt
from fastapi import HTTPException

ISSUER = "https://auth.codestra.co/realms/codestra"
AUDIENCE = "telnexa-gateway"
JWKS_URL = ISSUER + "/protocol/openid-connect/certs"
MAX_TOKEN_BYTES = 16384
MAX_JWKS_BYTES = 1048576
MAX_MACHINE_LIFETIME = 300
_CACHE_TTL = 300
_REFRESH_INTERVAL = 30
_cache = {"at": 0.0, "keys": {}}
_lock = threading.Lock()
_SCOPE_ALIASES = {
    "read": "sms.status.read",
    "sms.read": "sms.status.read",
    "messages:write": "sms.send",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "JWKS redirect denied", headers, fp)


def token_issuer(token: str) -> str:
    """Read an UNTRUSTED routing hint. This function never authenticates a token."""
    try:
        if len(token) > MAX_TOKEN_BYTES:
            raise ValueError("oversize token")
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("invalid token")
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
        issuer = claims.get("iss")
        if not isinstance(issuer, str) or not issuer:
            raise ValueError("issuer required")
        return issuer
    except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
        raise HTTPException(401, "invalid_access_token") from exc


def _load_keys() -> dict:
    # Fixed HTTPS URL, certificate verification and redirects disabled: JWT fields
    # and configuration cannot redirect the verifier to an attacker-controlled key.
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(JWKS_URL, timeout=5) as response:  # nosec B310 -- fixed HTTPS URL
            raw = response.read(MAX_JWKS_BYTES + 1)
        if len(raw) > MAX_JWKS_BYTES:
            raise ValueError("oversize JWKS")
        document = json.loads(raw)
        records = document.get("keys")
        if not isinstance(records, list) or not records:
            raise ValueError("missing keys")
        keys = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("invalid key")
            if record.get("kty") != "RSA" or record.get("use", "sig") != "sig":
                continue
            if record.get("alg", "RS256") != "RS256":
                continue
            kid = record.get("kid")
            if not isinstance(kid, str) or not kid or kid in keys:
                raise ValueError("invalid or duplicate key id")
            keys[kid] = record
        if not keys:
            raise ValueError("no supported signing keys")
        return keys
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(503, "codestra_identity_keys_unavailable") from exc


def _signing_key(kid: str):
    with _lock:
        age = time.monotonic() - _cache["at"]
        if (
            not _cache["keys"]
            or age >= _CACHE_TTL
            or (kid not in _cache["keys"] and age >= _REFRESH_INTERVAL)
        ):
            keys = _load_keys()
            _cache.update(at=time.monotonic(), keys=keys)
        record = _cache["keys"].get(kid)
    if record is None:
        raise HTTPException(401, "invalid_access_token")
    try:
        return jwt.PyJWK.from_dict(record, algorithm="RS256").key
    except (ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(503, "codestra_identity_keys_unavailable") from exc


def validate_codestra_token(token: str, tenant_id: str | None, required: str) -> dict:
    """Verify an explicitly enabled machine trust; roles never substitute for scopes."""
    if os.environ.get("CODESTRA_OIDC_ENABLED", "false") != "true":
        raise HTTPException(403, "codestra_identity_disabled")
    allowed_clients = {
        value.strip()
        for value in os.environ.get("CODESTRA_OIDC_ALLOWED_AZP", "").split(",")
        if value.strip()
    }
    if not allowed_clients:
        raise HTTPException(503, "codestra_client_allowlist_required")
    try:
        if len(token) > MAX_TOKEN_BYTES:
            raise ValueError("oversize token")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise ValueError("unsupported signing header")
        claims = jwt.decode(
            token,
            _signing_key(kid),
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": ["iss", "sub", "aud", "azp", "iat", "exp", "jti"],
                "strict_aud": True,
            },
        )
        for name in ("sub", "azp", "jti", "tenant_id", "account_id"):
            if not isinstance(claims.get(name), str) or not claims[name].strip():
                raise ValueError("missing identity binding")
        issued_at, expires_at = claims["iat"], claims["exp"]
        if type(issued_at) is not int or type(expires_at) is not int:
            raise ValueError("invalid NumericDate")
        if not 0 < expires_at - issued_at <= MAX_MACHINE_LIFETIME:
            raise ValueError("invalid machine token lifetime")
        scope = claims.get("scope", "")
        if not isinstance(scope, str):
            raise ValueError("invalid scope claim")
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, jwt.PyJWTError) as exc:
        raise HTTPException(401, "invalid_access_token") from exc
    if claims["azp"] not in allowed_clients:
        raise HTTPException(403, "client_identity_denied")
    if not tenant_id or claims["tenant_id"] != tenant_id:
        raise HTTPException(403, "tenant_or_account_binding_required")
    scopes = set(scope.split())
    needed = _SCOPE_ALIASES.get(required, required)
    if needed not in scopes:
        raise HTTPException(403, "insufficient_scope")
    return {
        "tenant_id": claims["tenant_id"],
        "account_id": claims["account_id"],
        "subject": claims["sub"],
        "roles": [],
        "scopes": sorted(scopes),
    }
