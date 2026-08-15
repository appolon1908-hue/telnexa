#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
echo "Jasmin console username: ${JASMIN_ADMIN_USER:-telnexa-admin}; password is in .env and will not be printed."
"${COMPOSE[@]}" exec jasmin telnet 127.0.0.1 8990
