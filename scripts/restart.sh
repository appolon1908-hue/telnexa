#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
"${COMPOSE[@]}" restart
"$REPO_DIR/scripts/health.sh"
