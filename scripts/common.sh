#!/usr/bin/env bash
set -euo pipefail
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT_NAME=${COMPOSE_PROJECT_NAME:-telnexa}
COMPOSE=(docker compose -p "$PROJECT_NAME" --project-directory "$REPO_DIR" --env-file "$REPO_DIR/.env" -f "$REPO_DIR/docker-compose.yml")
cd "$REPO_DIR"
test -f .env || { echo "Missing .env; copy .env.example and run scripts/generate-env.sh" >&2; exit 1; }
