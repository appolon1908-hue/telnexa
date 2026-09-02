#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
backup=${1:?usage: scripts/restore.sh BACKUP_DIRECTORY}
test -f "$backup/repository-config.tar.gz" || { echo "Invalid backup directory" >&2; exit 1; }
test -f "$backup/SHA256SUMS" || { echo "Backup integrity manifest is missing" >&2; exit 1; }
for artifact in billing.pgdump keycloak.pgdump rabbitmq-definitions.json runtime-secrets.tar.gz runtime-mtls.tar.gz; do
  test -f "$backup/$artifact" || { echo "Required recovery artifact is missing: $artifact" >&2; exit 1; }
done
backup=$(cd "$backup" && pwd)
(cd "$backup" && sha256sum --check SHA256SUMS)
python3 -m json.tool "$backup/rabbitmq-definitions.json" >/dev/null
echo "Restore is destructive to current named-volume contents. Set CONFIRM_RESTORE=YES after taking a fresh backup." >&2
test "${CONFIRM_RESTORE:-}" = YES || exit 2
"${COMPOSE[@]}" down
# Recovery data must not replace the reviewed deployment tooling currently
# checked out from Git.  start.sh also rejects a restored SOURCE_SHA that does
# not match that checkout, forcing an explicit approved source rollback.
tar -xzf "$backup/repository-config.tar.gz" --exclude=scripts -C "$REPO_DIR"
runtime_secret_dir=$(dirname "$(sed -n 's/^TELNEXA_PROVIDER_KEYS_FILE=//p' .env)")
middleware_ca=$(sed -n 's/^TELNEXA_MIDDLEWARE_CA_FILE=//p' .env)
middleware_cert=$(sed -n 's/^TELNEXA_MIDDLEWARE_CLIENT_CERT_FILE=//p' .env)
middleware_key=$(sed -n 's/^TELNEXA_MIDDLEWARE_CLIENT_KEY_FILE=//p' .env)
middleware_dir=$(dirname "$middleware_ca")
case "$runtime_secret_dir" in
  /etc/telnexa/secrets | /opt/telnexa/secrets) ;;
  *) echo "Runtime secret directory is outside the approved Telnexa roots" >&2; exit 1 ;;
esac
case "$middleware_dir" in
  /etc/telnexa/mtls/middleware | /opt/telnexa/mtls/middleware) ;;
  *) echo "mTLS directory is outside the approved Telnexa roots" >&2; exit 1 ;;
esac
test "$(dirname "$middleware_cert")" = "$middleware_dir"
test "$(dirname "$middleware_key")" = "$middleware_dir"
install -d -o root -g root -m 0700 "$runtime_secret_dir" "$middleware_dir"
tar -xzf "$backup/runtime-secrets.tar.gz" -C "$runtime_secret_dir"
tar -xzf "$backup/runtime-mtls.tar.gz" -C "$middleware_dir"
for volume in jasmin-config redis-data; do
  archive="$backup/${volume}.tar.gz"
  test -f "$archive" || continue
  docker volume create "${PROJECT_NAME}_${volume}" >/dev/null
  docker run --rm -v "${PROJECT_NAME}_${volume}:/target" -v "$backup:/backup:ro" alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce \
    sh -c "find /target -mindepth 1 -delete && tar -C /target -xzf /backup/${volume}.tar.gz"
done
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 120 \
  billing-db keycloak-db redis rabbitmq
for database in billing keycloak; do
  service="${database}-db"
  "${COMPOSE[@]}" exec -T "$service" sh -c \
    'dropdb --if-exists --force --username="$POSTGRES_USER" "$POSTGRES_DB" && createdb --username="$POSTGRES_USER" "$POSTGRES_DB"'
  "${COMPOSE[@]}" exec -T "$service" sh -c \
    'pg_restore --exit-on-error --no-owner --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    < "$backup/${database}.pgdump"
done
"${COMPOSE[@]}" exec -T rabbitmq rabbitmqctl --quiet import_definitions - \
  < "$backup/rabbitmq-definitions.json"
"$REPO_DIR/scripts/start.sh"
"$REPO_DIR/scripts/health.sh"
