#!/usr/bin/env bash
set -euo pipefail
server=http://keycloak:8080/auth
admin_password=$(cat /run/secrets/keycloak_admin_password)
/opt/keycloak/bin/kcadm.sh config credentials --server "$server" --realm master --user telnexa-bootstrap --password "$admin_password" >/dev/null
/opt/keycloak/bin/kcadm.sh update users/profile -r telnexa -f /opt/telnexa/user-profile.json
required_actions=$(
  /opt/keycloak/bin/kcadm.sh get authentication/required-actions -r telnexa \
    --fields alias --format csv --noquotes
)
if grep -qx 'VERIFY_PROFILE' <<<"$required_actions"; then
  /opt/keycloak/bin/kcadm.sh update authentication/required-actions/VERIFY_PROFILE \
    -r telnexa -s enabled=false -s defaultAction=false
fi
/opt/keycloak/bin/kcadm.sh update realms/telnexa \
  -s verifyEmail=true \
  -s bruteForceProtected=true \
  -s permanentLockout=false \
  -s failureFactor=10 \
  -s waitIncrementSeconds=60 \
  -s minimumQuickLoginWaitSeconds=60 \
  -s maxFailureWaitSeconds=900 \
  -s accessTokenLifespan=300 \
  -s revokeRefreshToken=true \
  -s refreshTokenMaxReuse=0 \
  -s ssoSessionIdleTimeout=1800 \
  -s ssoSessionMaxLifespan=28800 \
  -s otpPolicyType=totp \
  -s otpPolicyAlgorithm=HmacSHA256 \
  -s otpPolicyDigits=6 \
  -s otpPolicyPeriod=30 \
  -s eventsEnabled=true \
  -s eventsExpiration=2592000 \
  -s adminEventsEnabled=true \
  -s adminEventsDetailsEnabled=true
/opt/keycloak/bin/kcadm.sh update authentication/required-actions/CONFIGURE_TOTP \
  -r telnexa -s enabled=true -s defaultAction=true
/opt/keycloak/bin/kcadm.sh update authentication/required-actions/VERIFY_EMAIL \
  -r telnexa -s enabled=true -s defaultAction=true

portal_client_rows=$(
  /opt/keycloak/bin/kcadm.sh get clients -r telnexa \
    -q clientId=telnexa-portal --fields id --format csv --noquotes
)
mapfile -t portal_client_ids < <(sed '/^id$/d; /^$/d' <<<"$portal_client_rows")
if [[ "${#portal_client_ids[@]}" -ne 1 ]]; then
  echo "Expected exactly one Telnexa portal client identity" >&2
  exit 1
fi
portal_client_id=${portal_client_ids[0]}
case "$portal_client_id" in
  "" | *[!A-Za-z0-9-]*)
    echo "Telnexa portal client identity is missing or invalid" >&2
    exit 1
    ;;
esac
/opt/keycloak/bin/kcadm.sh update "clients/$portal_client_id" -r telnexa \
  -s 'redirectUris=["https://api.telnexa.co/*","https://app.telnexa.co/*"]' \
  -s 'webOrigins=["https://api.telnexa.co","https://app.telnexa.co"]'

realm=$(/opt/keycloak/bin/kcadm.sh get realms/telnexa \
  --fields verifyEmail,bruteForceProtected,revokeRefreshToken,refreshTokenMaxReuse,eventsEnabled,adminEventsEnabled,adminEventsDetailsEnabled)
for field in verifyEmail bruteForceProtected eventsEnabled adminEventsEnabled adminEventsDetailsEnabled; do
  grep -Eq '"'"$field"'"[[:space:]]*:[[:space:]]*true' <<<"$realm"
done
grep -Eq '"revokeRefreshToken"[[:space:]]*:[[:space:]]*true' <<<"$realm"
grep -Eq '"refreshTokenMaxReuse"[[:space:]]*:[[:space:]]*0' <<<"$realm"
totp=$(/opt/keycloak/bin/kcadm.sh get authentication/required-actions/CONFIGURE_TOTP -r telnexa \
  --fields enabled,defaultAction)
grep -Eq '"enabled"[[:space:]]*:[[:space:]]*true' <<<"$totp"
grep -Eq '"defaultAction"[[:space:]]*:[[:space:]]*true' <<<"$totp"
portal=$(/opt/keycloak/bin/kcadm.sh get "clients/$portal_client_id" -r telnexa \
  --fields redirectUris,webOrigins)
grep -Fq 'https://api.telnexa.co/*' <<<"$portal"
grep -Fq 'https://app.telnexa.co/*' <<<"$portal"
grep -Fq 'https://api.telnexa.co' <<<"$portal"
grep -Fq 'https://app.telnexa.co' <<<"$portal"
printf '%s\n' 'Keycloak tenant profile, MFA, brute-force, session, and audit controls configured'
