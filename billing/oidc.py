import json
import os
import time
import urllib.request
import jwt
from fastapi import HTTPException

from billing.codestra_identity import ISSUER as CODESTRA_ISSUER
from billing.codestra_identity import token_issuer, validate_codestra_token

_cache = {"at": 0.0, "keys": []}
ROLE_SCOPES = {
    "OWNER": {"*"},
    "ADMIN": {"*"},
    "BILLING": {"billing.read", "sms.read"},
    "DEVELOPER": {"sms.send", "sms.read", "sms.bulk", "sms.webhook", "sms.number.read"},
    "SUPPORT": {"sms.read", "sms.number.read"},
    "READ_ONLY": {"sms.read", "sms.number.read", "billing.read"},
}
ALIASES = {
    "read": "sms.read",
    "messages:write": "sms.send",
    "bulk:write": "sms.bulk",
    "webhooks:write": "sms.webhook",
    "senders:write": "sms.send",
    "contacts:write": "sms.send",
    "campaigns:write": "sms.bulk",
}


def _jwks():
    now = time.monotonic()
    if now - _cache["at"] > 300:
        issuer = os.environ["OIDC_ISSUER"].rstrip("/")
        jwks_url = os.environ.get("OIDC_JWKS_URL", issuer + "/protocol/openid-connect/certs")
        if jwks_url != "http://keycloak:8080/auth/realms/telnexa/protocol/openid-connect/certs":
            raise HTTPException(503, "canonical_identity_jwks_unavailable")
        # The value is compared to the sole private Keycloak URL immediately above.
        with urllib.request.urlopen(jwks_url, timeout=5) as response:  # nosec B310
            _cache.update(at=now, keys=json.load(response)["keys"])
    return {key["kid"]: key for key in _cache["keys"]}


def validate_bearer(authorization: str | None, tenant_id: str | None, required: str = "read"):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    # The untrusted issuer selects ONLY a fixed verifier, never a URL or grants.
    # Signature, issuer, audience, client, tenant and scope are checked there.
    if token_issuer(token) == CODESTRA_ISSUER:
        return validate_codestra_token(token, tenant_id, required)
    issuer = os.environ.get("OIDC_ISSUER", "").rstrip("/")
    audience = os.environ.get("OIDC_AUDIENCE", "telnexa-api")
    if issuer != "https://api.telnexa.co/auth/realms/telnexa" or audience != "telnexa-api":
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
            options={"require": ["exp", "iat", "jti", "sub", "iss", "aud", "azp"]},
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "invalid_access_token")
    allowed_clients = {
        value for value in os.environ.get("OIDC_ALLOWED_AZP", "").split(",") if value
    }
    azp = claims.get("azp")
    if not allowed_clients or azp not in allowed_clients:
        raise HTTPException(403, "client_identity_denied")
    bound_tenant = claims.get("tenant_id")
    account_id = claims.get("account_id")
    if not tenant_id or not bound_tenant or tenant_id != bound_tenant or not account_id:
        raise HTTPException(403, "tenant_or_account_binding_required")
    roles = set(claims.get("realm_access", {}).get("roles", []))
    scopes = set(claims.get("scope", "").split())
    grants = set(scopes)
    for role in roles:
        grants |= ROLE_SCOPES.get(role, set())
    needed = ALIASES.get(required, required)
    if "*" not in grants and needed not in grants:
        raise HTTPException(403, "insufficient_scope")
    return {
        "tenant_id": bound_tenant,
        "account_id": account_id,
        "subject": claims.get("sub"),
        "roles": sorted(roles),
        "scopes": sorted(scopes),
    }
