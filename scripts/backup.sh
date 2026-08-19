#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
umask 077
backup_root=${BACKUP_DIR:-$REPO_DIR/backups}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$backup_root/$stamp"
mkdir -p "$dest"
tar -czf "$dest/repository-config.tar.gz" --exclude=.git --exclude=backups --exclude='*.log' \
  docker-compose.yml .env config docker docs examples scripts README.md DEPLOYMENT_REPORT.md
"${COMPOSE[@]}" exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" SAVE' >/dev/null
"${COMPOSE[@]}" exec -T billing-db sh -c 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" "$POSTGRES_DB"' > "$dest/billing.pgdump"
jasmin_volume=$("${COMPOSE[@]}" config --volumes | grep 'jasmin-config')
redis_volume=$("${COMPOSE[@]}" config --volumes | grep 'redis-data')
for volume in "$jasmin_volume" "$redis_volume"; do
  docker run --rm -v "${PROJECT_NAME}_${volume}:/source:ro" -v "$dest:/backup" alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce \
    tar -C /source -czf "/backup/${volume}.tar.gz" .
done
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -mtime +"${BACKUP_RETENTION_DAYS:-14}" -print
echo "Backup created: $dest (contains .env secrets; protect and encrypt off-host)."
