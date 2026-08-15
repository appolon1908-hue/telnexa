#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
test -z "$(git status --porcelain)" || { echo "Refusing update with uncommitted repository changes" >&2; exit 1; }
"$REPO_DIR/scripts/backup.sh"
git pull --ff-only
"${COMPOSE[@]}" pull --ignore-buildable
"${COMPOSE[@]}" up -d --build --remove-orphans
"$REPO_DIR/scripts/health.sh"
