#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
umask 077
backup_root=${BACKUP_DIR:-$REPO_DIR/backups}
recipient_file=${TELNEXA_BACKUP_RECIPIENT_FILE:-/etc/telnexa/backup-recipient-public.asc}
staging_root=${TELNEXA_BACKUP_STAGING_ROOT:-/dev/shm}
off_host_dir=${TELNEXA_OFF_HOST_BACKUP_DIR:-}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$backup_root/telnexa-$stamp.tar.gz.gpg"
checksum="$archive.sha256"
test -r "$recipient_file" || { echo "Backup recipient public key is unavailable" >&2; exit 2; }
test "$(stat -f -c %T "$staging_root")" = tmpfs || { echo "Backup staging must be tmpfs" >&2; exit 2; }
test -n "$off_host_dir" && test -d "$off_host_dir" || { echo "A mounted off-host backup directory is required" >&2; exit 2; }
mountpoint -q "$off_host_dir" || { echo "Off-host backup directory must be a distinct mountpoint" >&2; exit 2; }
mkdir -p "$backup_root"
backup_source=$(findmnt -n -o SOURCE --target "$backup_root")
off_host_source=$(findmnt -n -o SOURCE --target "$off_host_dir")
test -n "$backup_source" && test -n "$off_host_source" && test "$backup_source" != "$off_host_source" \
  || { echo "Off-host backup storage must use a distinct mounted source" >&2; exit 2; }
stage=$(mktemp -d "$staging_root/telnexa-backup.XXXXXX")
case "$stage" in "$staging_root"/telnexa-backup.*) ;; *) echo "Unsafe backup staging path" >&2; exit 2 ;; esac
archive_tmp=$(mktemp "$backup_root/.telnexa-$stamp.XXXXXX.gpg")
cleanup() {
  find "$stage" -type f -exec shred -u {} + 2>/dev/null || true
  find "$stage" -depth -mindepth 1 -delete 2>/dev/null || true
  rmdir "$stage" 2>/dev/null || true
  test ! -e "$archive_tmp" || rm -f -- "$archive_tmp"
}
trap cleanup EXIT
test -d .secrets || { echo "Keycloak runtime secret directory is missing" >&2; exit 1; }
tar -czf "$stage/repository-config.tar.gz" --exclude=.git --exclude=backups --exclude='*.log' \
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
tar -C "$runtime_secret_dir" -czf "$stage/runtime-secrets.tar.gz" .
tar -C "$middleware_dir" -czf "$stage/runtime-mtls.tar.gz" \
  "$(basename "$middleware_ca")" "$(basename "$middleware_cert")" "$(basename "$middleware_key")"
"${COMPOSE[@]}" exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" SAVE' >/dev/null
"${COMPOSE[@]}" exec -T billing-db sh -c 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" "$POSTGRES_DB"' > "$stage/billing.pgdump"
"${COMPOSE[@]}" exec -T keycloak-db sh -c 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" "$POSTGRES_DB"' > "$stage/keycloak.pgdump"
"${COMPOSE[@]}" exec -T rabbitmq rabbitmqctl --quiet export_definitions - --format json \
  > "$stage/rabbitmq-definitions.json"
python3 -m json.tool "$stage/rabbitmq-definitions.json" >/dev/null
jasmin_volume=$("${COMPOSE[@]}" config --volumes | grep 'jasmin-config')
redis_volume=$("${COMPOSE[@]}" config --volumes | grep 'redis-data')
for volume in "$jasmin_volume" "$redis_volume"; do
  docker run --rm -v "${PROJECT_NAME}_${volume}:/source:ro" -v "$stage:/backup" alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce \
    tar -C /source -czf "/backup/${volume}.tar.gz" .
done
artifacts=(
  repository-config.tar.gz runtime-secrets.tar.gz runtime-mtls.tar.gz
  billing.pgdump keycloak.pgdump rabbitmq-definitions.json
  "${jasmin_volume}.tar.gz" "${redis_volume}.tar.gz"
)
(
  cd "$stage"
  sha256sum "${artifacts[@]}" > SHA256SUMS
  sha256sum --check SHA256SUMS
)
gpg_home="$stage/gnupg"
install -d -m 0700 "$gpg_home"
gpg --batch --homedir "$gpg_home" --import "$recipient_file" >/dev/null 2>&1
recipient=$(gpg --batch --homedir "$gpg_home" --with-colons --list-keys | awk -F: '$1=="fpr" {print $10; exit}')
case "$recipient" in *[!A-F0-9]*|'') echo "Backup recipient fingerprint is invalid" >&2; exit 2 ;; esac
test "${#recipient}" -ge 40 && test "${#recipient}" -le 64 || { echo "Backup recipient fingerprint is invalid" >&2; exit 2; }
tar -cz -C "$stage" "${artifacts[@]}" SHA256SUMS \
  | gpg --batch --yes --homedir "$gpg_home" --trust-model always --recipient "$recipient" --output "$archive_tmp" --encrypt
chmod 0600 "$archive_tmp"
mv -f -- "$archive_tmp" "$archive"
(cd "$backup_root" && sha256sum "$(basename "$archive")" > "$(basename "$checksum")")
chmod 0600 "$archive" "$checksum"
install -m 0600 "$archive" "$checksum" "$off_host_dir/"
(cd "$off_host_dir" && sha256sum --check "$(basename "$checksum")")
find "$backup_root" -maxdepth 1 -type f -name 'telnexa-*.tar.gz.gpg' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -print
printf 'Encrypted backup created locally and verified off-host: %s\n' "$archive"
