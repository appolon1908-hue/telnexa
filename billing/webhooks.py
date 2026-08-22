import base64
import hashlib
import ipaddress
import os
import socket
from urllib.parse import urlparse
from cryptography.fernet import Fernet, InvalidToken


def _fernet():
    secret = os.environ.get("BILLING_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("BILLING_JWT_SECRET must contain at least 32 characters")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("webhook secret decryption failed") from exc


def validate_webhook_url(value: str, resolve: bool = True) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("webhook_url_must_be_public_https_443")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError("webhook_url_private_host")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        if not resolve:
            return value
        try:
            addresses = {
                ipaddress.ip_address(x[4][0])
                for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError("webhook_url_unresolvable") from exc
    if not addresses or any(
        a.is_private
        or a.is_loopback
        or a.is_link_local
        or a.is_multicast
        or a.is_reserved
        or a.is_unspecified
        for a in addresses
    ):
        raise ValueError("webhook_url_private_address")
    return value
