#!/bin/sh
set -eu
exec redis-server /etc/redis/redis.conf --requirepass "$REDIS_PASSWORD"
