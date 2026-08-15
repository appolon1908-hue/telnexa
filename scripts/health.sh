#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
failed=0
deadline=$((SECONDS + ${HEALTH_WAIT_SECONDS:-120}))
for service in rabbitmq redis jasmin webhook-relay nginx prometheus node-exporter; do
  cid=$("${COMPOSE[@]}" ps -q "$service")
  if [ -z "$cid" ]; then echo "FAIL $service: not running"; failed=1; continue; fi
  while true; do
    state=$(docker inspect -f '{{.State.Status}}' "$cid")
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")
    if [ "$state" = running ] && { [ "$health" = healthy ] || [ "$health" = none ]; }; then break; fi
    [ "$SECONDS" -ge "$deadline" ] && break
    sleep 2
  done
  if [ "$state" != running ] || { [ "$health" != healthy ] && [ "$health" != none ]; }; then
    echo "FAIL $service: state=$state health=$health"; failed=1
  else
    echo "PASS $service: state=$state health=$health"
  fi
done
"${COMPOSE[@]}" exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" ping' | grep -q PONG || failed=1
"${COMPOSE[@]}" exec -T rabbitmq rabbitmq-diagnostics -q ping >/dev/null || failed=1
"${COMPOSE[@]}" exec -T jasmin python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1401/ping', timeout=4)" || failed=1
disk=$(df -P "$REPO_DIR" | awk 'NR==2 {gsub("%", "", $5); print $5}')
if [ "$disk" -ge "${DISK_CRITICAL_PERCENT:-85}" ]; then echo "FAIL disk usage: ${disk}%"; failed=1; else echo "PASS disk usage: ${disk}%"; fi
exit "$failed"
