#!/usr/bin/env bash
set -euo pipefail
server=http://keycloak:8080/auth
admin_password=$(cat /run/secrets/keycloak_admin_password)
/opt/keycloak/bin/kcadm.sh config credentials --server "$server" --realm master --user telnexa-bootstrap --password "$admin_password" >/dev/null
/opt/keycloak/bin/kcadm.sh update users/profile -r telnexa -f /opt/telnexa/user-profile.json
/opt/keycloak/bin/kcadm.sh update authentication/required-actions/VERIFY_PROFILE -r telnexa -s enabled=false -s defaultAction=false
/opt/keycloak/bin/kcadm.sh update realms/telnexa \
  -s verifyEmail=true \
  -s bruteForceProtected=true \
  -s permanentLockout=false \
  -s failureFactor=10 \
  -s waitIncrementSeconds=60 \
  -s minimumQuickLoginWaitSeconds=60 \
  -s maxFailureWaitSeconds=900 \
  -s accessTokenLifespan=300 \
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

realm=$(/opt/keycloak/bin/kcadm.sh get realms/telnexa \
  --fields verifyEmail,bruteForceProtected,eventsEnabled,adminEventsEnabled,adminEventsDetailsEnabled)
for field in verifyEmail bruteForceProtected eventsEnabled adminEventsEnabled adminEventsDetailsEnabled; do
  grep -Eq '"'"$field"'"[[:space:]]*:[[:space:]]*true' <<<"$realm"
done
totp=$(/opt/keycloak/bin/kcadm.sh get authentication/required-actions/CONFIGURE_TOTP -r telnexa \
  --fields enabled,defaultAction)
grep -Eq '"enabled"[[:space:]]*:[[:space:]]*true' <<<"$totp"
grep -Eq '"defaultAction"[[:space:]]*:[[:space:]]*true' <<<"$totp"
printf '%s\n' 'Keycloak tenant profile, MFA, brute-force, session, and audit controls configured'
