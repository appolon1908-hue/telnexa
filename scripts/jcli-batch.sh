#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
"${COMPOSE[@]}" exec -T jasmin /usr/local/bin/telnexa-jcli-batch
