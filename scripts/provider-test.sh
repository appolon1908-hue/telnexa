#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
provider=${1:?usage: scripts/provider-test.sh PROVIDER_ID [--send AUTHORIZED_NUMBER]}
printf 'smppccm -l\nstats --smppc %s\n' "$provider" | "$REPO_DIR/scripts/jcli-batch.sh"
if [ "${2:-}" != --send ]; then
  echo "Inspection only; no SMS sent. Use --send with an authorized test number after verifying credentials and route."
  exit 0
fi
number=${3:?authorized destination required}
"${COMPOSE[@]}" exec -T -e TEST_DESTINATION="$number" jasmin python - <<'PY'
import os, urllib.parse, urllib.request
query = urllib.parse.urlencode({
    "username": os.environ["JASMIN_API_USER"], "password": os.environ["JASMIN_API_PASSWORD"],
    "to": os.environ["TEST_DESTINATION"], "from": "Telnexa", "content": "Authorized Telnexa provider test",
})
print(urllib.request.urlopen("http://127.0.0.1:1401/send?" + query, timeout=20).read().decode())
PY
