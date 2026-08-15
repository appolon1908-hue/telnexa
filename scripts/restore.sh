#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
backup=${1:?usage: scripts/restore.sh BACKUP_DIRECTORY}
test -f "$backup/repository-config.tar.gz" || { echo "Invalid backup directory" >&2; exit 1; }
echo "Restore is destructive to current named-volume contents. Set CONFIRM_RESTORE=YES after taking a fresh backup." >&2
test "${CONFIRM_RESTORE:-}" = YES || exit 2
"${COMPOSE[@]}" down
tar -xzf "$backup/repository-config.tar.gz" -C "$REPO_DIR"
for volume in jasmin-config redis-data; do
  archive="$backup/${volume}.tar.gz"
  test -f "$archive" || continue
  docker volume create "${PROJECT_NAME}_${volume}" >/dev/null
  docker run --rm -v "${PROJECT_NAME}_${volume}:/target" -v "$backup:/backup:ro" alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce \
    sh -c "find /target -mindepth 1 -delete && tar -C /target -xzf /backup/${volume}.tar.gz"
done
"${COMPOSE[@]}" up -d --build
"$REPO_DIR/scripts/health.sh"
