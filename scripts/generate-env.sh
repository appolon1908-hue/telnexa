#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo "Run with sudo so runtime credentials are root-owned." >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
env_file="$repo/.env"
secret_dir="${TELNEXA_RUNTIME_SECRET_DIR:-/etc/telnexa/secrets}"
install -d -o root -g root -m 0700 "$secret_dir"
test -f "$env_file" || cp "$repo/.env.example" "$env_file"
chmod 0600 "$env_file"
for key in RABBITMQ_PASSWORD REDIS_PASSWORD JASMIN_ADMIN_PASSWORD JASMIN_API_PASSWORD WEBHOOK_HMAC_SECRET BILLING_DB_PASSWORD BILLING_JWT_SECRET BILLING_ADMIN_TOKEN BILLING_MIDDLEWARE_API_KEY BILLING_MIDDLEWARE_HMAC_SECRET GRAFANA_ADMIN_PASSWORD; do
  current=$(sed -n "s/^${key}=//p" "$env_file")
  if [ -z "$current" ] || [ "$current" = GENERATE_ME ]; then
    if [ "$key" = JASMIN_API_PASSWORD ]; then
      # Jasmin 0.11 HTTP credential validation accepts a maximum of 16 characters.
      value=$(openssl rand -hex 8)
    else
      value=$(openssl rand -base64 36 | tr -d '\n')
    fi
    escaped=$(printf '%s' "$value" | sed 's/[&|]/\\&/g')
    if grep -q "^${key}=" "$env_file"; then
      sed -i "s|^${key}=.*|${key}=${escaped}|" "$env_file"
    else
      printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
    fi
  fi
done
metrics_file="$secret_dir/metrics-token"
provider_token_file="$secret_dir/provider-source-token"
provider_registry_file="$secret_dir/provider-keys.json"
[[ -s "$metrics_file" ]] || openssl rand -hex 32 | install -o root -g root -m 0600 /dev/stdin "$metrics_file"
[[ -s "$provider_token_file" ]] || openssl rand -hex 32 | install -o root -g root -m 0600 /dev/stdin "$provider_token_file"
provider_digest=$(sha256sum "$provider_token_file" | awk '{print $1}')
printf '{"keys":[{"id":"jasmin-primary","enabled":true,"sha256":"%s"}]}\n' "$provider_digest" |
  install -o root -g root -m 0600 /dev/stdin "$provider_registry_file"
set_value() {
  local key=$1 value=$2 escaped
  escaped=$(printf '%s' "$value" | sed 's/[&|]/\\&/g')
  if grep -q "^${key}=" "$env_file"; then sed -i "s|^${key}=.*|${key}=${escaped}|" "$env_file"
  else printf '\n%s=%s\n' "$key" "$value" >> "$env_file"; fi
}
set_value TELNEXA_PROVIDER_KEYS_FILE "$provider_registry_file"
set_value TELNEXA_METRICS_TOKEN_FILE "$metrics_file"
set_value OIDC_ALLOWED_AZP "${OIDC_ALLOWED_AZP:-telnexa-portal}"
chown root:root "$env_file"
chmod 0600 "$env_file"
echo "Generated root-owned runtime credentials and .env without displaying secret values."
