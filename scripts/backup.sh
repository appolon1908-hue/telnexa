#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
umask 077
backup_root=${BACKUP_DIR:-$REPO_DIR/backups}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$backup_root/$stamp"
mkdir -p "$dest"
test -d .secrets || { echo "Keycloak runtime secret directory is missing" >&2; exit 1; }
tar -czf "$dest/repository-config.tar.gz" --exclude=.git --exclude=backups --exclude='*.log' \
  docker-compose.yml .env .secrets config docker docs examples scripts README.md DEPLOYMENT_REPORT.md
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
test -d "$runtime_secret_dir" || { echo "Runtime secret directory is missing" >&2; exit 1; }
test "$(dirname "$middleware_cert")" = "$middleware_dir"
test "$(dirname "$middleware_key")" = "$middleware_dir"
for file in "$middleware_ca" "$middleware_cert" "$middleware_key"; do test -s "$file"; done
tar -C "$runtime_secret_dir" -czf "$dest/runtime-secrets.tar.gz" .
tar -C "$middleware_dir" -czf "$dest/runtime-mtls.tar.gz" \
  "$(basename "$middleware_ca")" "$(basename "$middleware_cert")" "$(basename "$middleware_key")"
"${COMPOSE[@]}" exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" SAVE' >/dev/null
"${COMPOSE[@]}" exec -T billing-db sh -c 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" "$POSTGRES_DB"' > "$dest/billing.pgdump"
"${COMPOSE[@]}" exec -T keycloak-db sh -c 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" "$POSTGRES_DB"' > "$dest/keycloak.pgdump"
"${COMPOSE[@]}" exec -T rabbitmq rabbitmqctl --quiet export_definitions - --format json \
  > "$dest/rabbitmq-definitions.json"
python3 -m json.tool "$dest/rabbitmq-definitions.json" >/dev/null
jasmin_volume=$("${COMPOSE[@]}" config --volumes | grep 'jasmin-config')
redis_volume=$("${COMPOSE[@]}" config --volumes | grep 'redis-data')
for volume in "$jasmin_volume" "$redis_volume"; do
  docker run --rm -v "${PROJECT_NAME}_${volume}:/source:ro" -v "$dest:/backup" alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce \
    tar -C /source -czf "/backup/${volume}.tar.gz" .
done
(
  cd "$dest"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum --check SHA256SUMS
)
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime +"${BACKUP_RETENTION_DAYS:-14}" -print
echo "Backup created: $dest (contains secrets; protect and encrypt off-host)."
