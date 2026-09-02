#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
if grep -q '=GENERATE_ME$' .env; then
  echo "Refusing insecure placeholder secrets; run scripts/generate-env.sh" >&2
  exit 1
fi
if grep -Eq '^TELNEXA_[A-Z0-9_]*_IMAGE=example\.invalid/|@sha256:0{64}$' .env; then
  echo "Refusing placeholder images; install approved immutable release digests." >&2
  exit 1
fi
configured_source=$(sed -n 's/^SOURCE_SHA=//p' .env)
checkout_source=$(git rev-parse HEAD)
if [[ ! "$configured_source" =~ ^[0-9a-f]{40}$ ]] || [ "$configured_source" != "$checkout_source" ]; then
  echo "Refusing source/runtime drift; regenerate .env from the exact release checkout." >&2
  exit 1
fi
"${COMPOSE[@]}" pull
"${COMPOSE[@]}" up -d --no-build
"$REPO_DIR/scripts/health.sh"
