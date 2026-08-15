#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
env_file="$repo/.env"
test -f "$env_file" || cp "$repo/.env.example" "$env_file"
chmod 0600 "$env_file"
for key in RABBITMQ_PASSWORD REDIS_PASSWORD JASMIN_ADMIN_PASSWORD JASMIN_API_PASSWORD WEBHOOK_HMAC_SECRET; do
  current=$(sed -n "s/^${key}=//p" "$env_file")
  if [ -z "$current" ] || [ "$current" = GENERATE_ME ]; then
    if [ "$key" = JASMIN_API_PASSWORD ]; then
      # Jasmin 0.11 HTTP credential validation accepts a maximum of 16 characters.
      value=$(openssl rand -hex 8)
    else
      value=$(openssl rand -base64 36 | tr -d '\n')
    fi
    escaped=$(printf '%s' "$value" | sed 's/[&|]/\\&/g')
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$env_file"
  fi
done
echo "Generated missing secrets in .env (mode 0600). Review domains and LETSENCRYPT_EMAIL before deployment."
