import json
import os
import time
import urllib.request

import jwt
from fastapi import HTTPException

CANONICAL_ISSUER = "https://auth.codestra.co/realms/codestra"
CANONICAL_AUDIENCE = "telnexa-gateway"
MAX_MACHINE_TOKEN_LIFETIME_SECONDS = 300
_cache = {"at": 0.0, "keys": []}
ROLE_SCOPES = {
    "OWNER": {"*"},
    "ADMIN": {"*"},
    "BILLING": {"billing.read", "sms.read", "sms.status.read"},
    "DEVELOPER": {
        "sms.send",
        "sms.read",
        "sms.status.read",
        "sms.bulk",
        "sms.webhook",
        "sms.number.read",
    },
    "SUPPORT": {"sms.read", "sms.status.read", "sms.number.read"},
    "READ_ONLY": {"sms.read", "sms.status.read", "sms.number.read", "billing.read"},
}
ALIASES = {
    "read": "sms.status.read",
    "messages:write": "sms.send",
    "bulk:write": "sms.bulk",
    "webhooks:write": "sms.webhook",
    "senders:write": "sms.send",
    "contacts:write": "sms.send",
    "campaigns:write": "sms.bulk",
}
SCOPE_EQUIVALENTS = {
    "sms.read": {"sms.read", "sms.status.read"},
    "sms.status.read": {"sms.read", "sms.status.read"},
}


def _jwks():
    now = time.monotonic()
    if now - _cache["at"] > 300:
        issuer = os.environ.get("OIDC_ISSUER", CANONICAL_ISSUER).rstrip("/")
        if issuer != CANONICAL_ISSUER:
            raise HTTPException(503, "canonical_identity_unavailable")
        with urllib.request.urlopen(
            issuer + "/protocol/openid-connect/certs", timeout=5
        ) as response:
            document = json.load(response)
        keys = document.get("keys")
        if not isinstance(keys, list) or not keys:
            raise HTTPException(503, "identity_keys_unavailable")
        _cache.update(at=now, keys=keys)
    return {key["kid"]: key for key in _cache["keys"] if isinstance(key, dict) and key.get("kid")}


def _validate_machine_token_lifetime(claims):
    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, "invalid_access_token")
    lifetime = expires_at - issued_at
    now = int(time.time())
    if lifetime <= 0 or lifetime > MAX_MACHINE_TOKEN_LIFETIME_SECONDS:
        raise HTTPException(401, "machine_token_lifetime_invalid")
    if issued_at > now + 60:
        raise HTTPException(401, "machine_token_issued_in_future")


def validate_bearer(authorization: str | None, tenant_id: str | None, required: str = "read"):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    issuer = os.environ.get("OIDC_ISSUER", CANONICAL_ISSUER).rstrip("/")
    audience = os.environ.get("OIDC_AUDIENCE", CANONICAL_AUDIENCE)
    if issuer != CANONICAL_ISSUER or audience != CANONICAL_AUDIENCE:
        raise HTTPException(503, "canonical_identity_unavailable")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError("unsupported_token_header")
        key = jwt.PyJWK.from_dict(_jwks()[header["kid"]]).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={
                "require": ["exp", "iat", "jti", "sub", "iss", "aud", "azp"],
            },
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "invalid_access_token")
    _validate_machine_token_lifetime(claims)

    allowed_clients = {
        value.strip()
        for value in os.environ.get("OIDC_ALLOWED_AZP", "").split(",")
        if value.strip()
    }
    azp = claims.get("azp")
    if not allowed_clients or azp not in allowed_clients:
        raise HTTPException(403, "client_identity_denied")
    bound_tenant = claims.get("tenant_id")
    account_id = claims.get("account_id")
    if not tenant_id or not bound_tenant or tenant_id != bound_tenant or not account_id:
        raise HTTPException(403, "tenant_or_account_binding_required")
    roles = set(claims.get("realm_access", {}).get("roles", []))
    scopes = set(str(claims.get("scope", "")).split())
    grants = set(scopes)
    for role in roles:
        grants |= ROLE_SCOPES.get(role, set())
    needed = ALIASES.get(required, required)
    acceptable = SCOPE_EQUIVALENTS.get(needed, {needed})
    if "*" not in grants and not acceptable.intersection(grants):
        raise HTTPException(403, "insufficient_scope")
    return {
        "tenant_id": bound_tenant,
        "account_id": account_id,
        "subject": claims.get("sub"),
        "authorized_party": azp,
        "audience": CANONICAL_AUDIENCE,
        "roles": sorted(roles),
        "scopes": sorted(scopes),
        "token_id": claims.get("jti"),
    }
