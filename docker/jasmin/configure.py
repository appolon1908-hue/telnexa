#!/usr/bin/env python3
"""Materialize Jasmin config from environment without logging secrets."""

import hashlib
import os
import re
from pathlib import Path


def required(name):
    value = os.environ.get(name, "")
    if not value or value == "GENERATE_ME":
        raise SystemExit(f"Required environment variable {name} is not securely configured")
    return value


def patch_section(text, name, values):
    match = re.search(rf"(?ms)^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text)
    if not match:
        return text
    block = match.group(0)
    for key, value in values.items():
        pattern = rf"(?m)^#?{re.escape(key)}\s*=.*$"
        line = f"{key} = {value}"
        block = (
            re.sub(pattern, line, block, count=1)
            if re.search(pattern, block)
            else block + line + "\n"
        )
    return text[: match.start()] + block + text[match.end() :]


amqp = {
    "host": os.environ.get("AMQP_BROKER_HOST", "rabbitmq"),
    "port": os.environ.get("AMQP_BROKER_PORT", "5672"),
    "username": required("RABBITMQ_USER"),
    "password": required("RABBITMQ_PASSWORD"),
    "vhost": "/",
}
redis = {
    "host": os.environ.get("REDIS_CLIENT_HOST", "redis"),
    "port": os.environ.get("REDIS_CLIENT_PORT", "6379"),
    "password": required("REDIS_PASSWORD"),
    "dbid": "0",
}

for path in Path("/etc/jasmin").glob("*.cfg"):
    text = path.read_text()
    text = patch_section(text, "amqp-broker", amqp)
    text = patch_section(text, "redis-client", redis)
    if path.name == "jasmin.cfg":
        admin_password = required("JASMIN_ADMIN_PASSWORD")
        text = patch_section(
            text,
            "jcli",
            {
                "bind": "0.0.0.0",
                "authentication": "True",
                "admin_username": os.environ.get("JASMIN_ADMIN_USER", "telnexa-admin"),
                "admin_password": hashlib.md5(admin_password.encode()).hexdigest(),
            },
        )
        text = patch_section(
            text, "http-api", {"bind": "0.0.0.0", "port": "1401", "log_privacy": "True"}
        )
        text = patch_section(
            text, "smpp-server", {"bind": "0.0.0.0", "port": "2775", "log_privacy": "True"}
        )
    path.write_text(text)
    path.chmod(0o600)
