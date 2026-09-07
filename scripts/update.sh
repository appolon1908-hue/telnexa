#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
test -z "$(git status --porcelain)" || { echo "Refusing update with uncommitted repository changes" >&2; exit 1; }
"$REPO_DIR/scripts/backup.sh"
git pull --ff-only
"$REPO_DIR/scripts/generate-env.sh"
"$REPO_DIR/scripts/start.sh"
