#!/usr/bin/env bash
set -euo pipefail
server=http://keycloak:8080/auth
admin_password=$(cat /run/secrets/keycloak_admin_password)
/opt/keycloak/bin/kcadm.sh config credentials --server "$server" --realm master --user telnexa-bootstrap --password "$admin_password" >/dev/null
/opt/keycloak/bin/kcadm.sh update users/profile -r telnexa -f /opt/telnexa/user-profile.json
/opt/keycloak/bin/kcadm.sh update authentication/required-actions/VERIFY_PROFILE -r telnexa -s enabled=false -s defaultAction=false
printf '%s\n' 'Keycloak tenant profile and required actions configured'
