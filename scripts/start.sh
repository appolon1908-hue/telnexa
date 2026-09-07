#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
if grep -q '=GENERATE_ME$' .env; then
  echo "Refusing insecure placeholder secrets; run scripts/generate-env.sh" >&2
  exit 1
fi
release_images=(
  TELNEXA_KEYCLOAK_IMAGE
  TELNEXA_JASMIN_IMAGE
  TELNEXA_WEBHOOK_RELAY_IMAGE
  TELNEXA_NGINX_IMAGE
  TELNEXA_BILLING_IMAGE
  TELNEXA_GRAFANA_IMAGE
)
for name in "${release_images[@]}"; do
  image=$(sed -n "s/^${name}=//p" .env)
  if [[ ! "$image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || [[ "$image" == example.invalid/* ]] \
    || [[ "$image" =~ @sha256:0{64}$ ]]; then
    echo "Refusing mutable or invalid image reference for ${name}." >&2
    exit 1
  fi
done
configured_source=$(sed -n 's/^SOURCE_SHA=//p' .env)
checkout_source=$(git rev-parse HEAD)
if [[ ! "$configured_source" =~ ^[0-9a-f]{40}$ ]] || [ "$configured_source" != "$checkout_source" ]; then
  echo "Refusing source/runtime drift; regenerate .env from the exact release checkout." >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  echo "Refusing deployment from a modified source worktree." >&2
  exit 1
fi
"${COMPOSE[@]}" pull
application_images=(
  TELNEXA_KEYCLOAK_IMAGE
  TELNEXA_JASMIN_IMAGE
  TELNEXA_WEBHOOK_RELAY_IMAGE
  TELNEXA_NGINX_IMAGE
  TELNEXA_BILLING_IMAGE
)
for name in "${application_images[@]}"; do
  image=$(sed -n "s/^${name}=//p" .env)
  image_source=$(docker image inspect "$image" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
  if [ "$image_source" != "$checkout_source" ]; then
    echo "Refusing source/image drift for ${name}." >&2
    exit 1
  fi
done
existing_jasmin=$("${COMPOSE[@]}" ps -q jasmin)
existing_rabbitmq=$("${COMPOSE[@]}" ps -q rabbitmq)
restart_stopped_jasmin=false
recover_stopped_jasmin() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$restart_stopped_jasmin" = true ]; then
    echo "Release migration failed; restarting the previous Jasmin container." >&2
    docker start "$existing_jasmin" >/dev/null || true
  fi
}
trap recover_stopped_jasmin EXIT
if [ -n "$existing_jasmin$existing_rabbitmq" ]; then
  "$REPO_DIR/scripts/backup.sh"
fi
if [ -n "$existing_rabbitmq" ]; then
  "${COMPOSE[@]}" exec -T rabbitmq rabbitmq-plugins enable rabbitmq_management
fi
"${COMPOSE[@]}" run --rm -e MIGRATION_MODE=check jasmin-topology-migrate
if [ -n "$existing_jasmin" ]; then
  "${COMPOSE[@]}" stop jasmin
  restart_stopped_jasmin=true
fi
"${COMPOSE[@]}" run --rm jasmin-volume-migrate
"${COMPOSE[@]}" run --rm -e MIGRATION_MODE=apply jasmin-topology-migrate
"${COMPOSE[@]}" up -d --no-build
"$REPO_DIR/scripts/health.sh"
restart_stopped_jasmin=false
