#!/usr/bin/env bash
set -euo pipefail

if [ ! -f /etc/jasmin/jasmin.cfg ]; then
  cp -a /opt/telnexa-default-config/. /etc/jasmin/
fi
/usr/local/bin/telnexa-configure
python - <<'PY'
from pathlib import Path
for lock in (Path('/tmp/jasmind.lock'), Path('/tmp/interceptord.lock'), Path('/tmp/telnexa-ready')):
    lock.unlink(missing_ok=True)
PY

interceptord.py &
interceptor_pid=$!
jasmind.py --enable-interceptor-client --enable-dlr-thrower --enable-dlr-lookup \
  -u "$JASMIN_ADMIN_USER" -p "$JASMIN_ADMIN_PASSWORD" &
jasmin_pid=$!

shutdown() {
  kill -TERM "$jasmin_pid" "$interceptor_pid" 2>/dev/null || true
  wait "$jasmin_pid" "$interceptor_pid" 2>/dev/null || true
}
trap shutdown TERM INT

for attempt in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1401/ping', timeout=2)" 2>/dev/null; then
    /usr/local/bin/telnexa-bootstrap
    python -c "from pathlib import Path; Path('/tmp/telnexa-ready').touch(mode=0o600)"
    break
  fi
  if ! kill -0 "$jasmin_pid" 2>/dev/null; then
    wait "$jasmin_pid"
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "Jasmin did not become ready" >&2
    exit 1
  fi
  sleep 2
done

wait "$jasmin_pid"
