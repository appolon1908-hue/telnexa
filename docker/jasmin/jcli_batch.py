#!/usr/bin/env python3
"""Run newline-delimited jCli commands from stdin without printing credentials."""

import os
import sys
import telnetlib

session = telnetlib.Telnet("127.0.0.1", 8990, timeout=20)
session.read_until(b"Username:", timeout=20)
session.write(os.environ.get("JASMIN_ADMIN_USER", "telnexa-admin").encode() + b"\n")
session.read_until(b"Password:", timeout=20)
session.write(os.environ["JASMIN_ADMIN_PASSWORD"].encode() + b"\n")
session.read_until(b"jcli :", timeout=20)
interactive = False
for raw in sys.stdin:
    command = raw.rstrip("\n")
    if not command or command.startswith("#"):
        continue
    session.write(command.encode() + b"\n")
    index, _, output = session.expect([b"jcli :", b"> "], timeout=20)
    if index < 0:
        raise SystemExit("Timed out waiting for jCli prompt")
    interactive = index == 1
    sys.stdout.write(output.decode(errors="replace").replace(command, "[command]"))
if interactive:
    session.write(b"ko\n")
    session.read_until(b"jcli :", timeout=20)
session.write(b"persist\n")
session.read_until(b"jcli :", timeout=20)
session.write(b"quit\n")
session.close()
