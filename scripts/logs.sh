#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
"${COMPOSE[@]}" logs --tail="${TAIL:-200}" -f "$@"
