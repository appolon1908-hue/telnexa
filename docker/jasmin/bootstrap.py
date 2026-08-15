#!/usr/bin/env python3
"""Idempotently create the middleware Jasmin group/user and quotas."""
import os
import telnetlib

admin_user = os.environ.get("JASMIN_ADMIN_USER", "telnexa-admin")
admin_password = os.environ["JASMIN_ADMIN_PASSWORD"]
api_group = os.environ.get("JASMIN_API_GROUP", "telnexa-api")
api_user = os.environ.get("JASMIN_API_USER", "middleware")
api_password = os.environ["JASMIN_API_PASSWORD"]

session = telnetlib.Telnet("127.0.0.1", 8990, timeout=20)
session.read_until(b"Username:", timeout=20)
session.write(admin_user.encode() + b"\n")
session.read_until(b"Password:", timeout=20)
session.write(admin_password.encode() + b"\n")
session.read_until(b"jcli :", timeout=20)


def command(value, prompt=b"jcli :"):
    session.write(value.encode() + b"\n")
    return session.read_until(prompt, timeout=20).decode(errors="replace")


groups = command("group -l")
users = command("user -l")
if api_group not in groups:
    command("group -a", b"> ")
    command(f"gid {api_group}", b"> ")
    command("ok")
if api_user not in users:
    command("user -a", b"> ")
    command(f"username {api_user}", b"> ")
    command(f"password {api_password}", b"> ")
    command(f"gid {api_group}", b"> ")
    command(f"uid {api_user}", b"> ")
    command("ok")
command(f"user -u {api_user}", b"> ")
command(f"password {api_password}", b"> ")
command(f"mt_messaging_cred quota http_throughput {os.environ.get('JASMIN_HTTP_THROUGHPUT', '20')}", b"> ")
command(f"mt_messaging_cred quota smpps_throughput {os.environ.get('JASMIN_SMPPS_THROUGHPUT', '20')}", b"> ")
command("ok")
command("persist")
session.write(b"quit\n")
session.close()
