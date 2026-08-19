#!/bin/sh
set -eu

for flag in LIVE_SMS_DELIVERY LIVE_EMAIL_DELIVERY LIVE_PSTN_DIALING; do
    eval "present=\${$flag+x}"
    eval "value=\${$flag-}"
    if [ "$present" != x ] || [ "$value" != false ]; then
        echo "startup denied: $flag must be exact lowercase false" >&2
        exit 78
    fi
done

exec "$@"
