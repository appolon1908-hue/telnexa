#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
set -a; source .env; set +a
public_ip=$(curl -4 -fsS --max-time 10 https://api.ipify.org)
domains=("$SMS_DOMAIN" "$API_DOMAIN" "$PORTAL_DOMAIN" "$ADMIN_DOMAIN" "$STATUS_DOMAIN")
for domain in "${domains[@]}"; do
  answer=$(dig +short A "$domain" | sort -u)
  grep -qx "$public_ip" <<<"$answer" || { echo "$domain does not resolve to $public_ip; TLS not attempted" >&2; exit 1; }
done
test -n "$LETSENCRYPT_EMAIL" && [ "$LETSENCRYPT_EMAIL" != operations@example.com ] || { echo "Set a real LETSENCRYPT_EMAIL in .env" >&2; exit 1; }
"${COMPOSE[@]}" --profile tls run --rm certbot certonly --webroot -w /var/www/certbot \
  --non-interactive --agree-tos --email "$LETSENCRYPT_EMAIL" --cert-name "$SMS_DOMAIN" \
  -d "$SMS_DOMAIN" -d "$API_DOMAIN" -d "$PORTAL_DOMAIN" -d "$ADMIN_DOMAIN" -d "$STATUS_DOMAIN"
"${COMPOSE[@]}" restart nginx
echo "TLS installed. Test with: curl -I https://$SMS_DOMAIN/healthz"
