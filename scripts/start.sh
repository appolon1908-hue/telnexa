#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
if grep -q '=GENERATE_ME$' .env; then
  echo "Refusing insecure placeholder secrets; run scripts/generate-env.sh" >&2
  exit 1
fi
"${COMPOSE[@]}" up -d --build
"$REPO_DIR/scripts/health.sh"
