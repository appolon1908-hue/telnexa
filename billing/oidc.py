import json
import os
import time
import urllib.request
import jwt
from fastapi import HTTPException

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
        with urllib.request.urlopen(
            issuer + "/protocol/openid-connect/certs", timeout=5
        ) as response:
            _cache.update(at=now, keys=json.load(response)["keys"])
    return {key["kid"]: key for key in _cache["keys"]}


def validate_bearer(authorization: str | None, tenant_id: str | None, required: str = "read"):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    issuer = os.environ.get("OIDC_ISSUER", "").rstrip("/")
    audience = os.environ.get("OIDC_AUDIENCE", "codestra-api")
    if issuer != "https://auth.codestra.co/realms/codestra":
        raise HTTPException(503, "canonical_identity_unavailable")
    try:
        header = jwt.get_unverified_header(token)
        key = jwt.PyJWK.from_dict(_jwks()[header["kid"]]).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require_exp": True, "require_iat": True, "require_jti": True},
        )
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
