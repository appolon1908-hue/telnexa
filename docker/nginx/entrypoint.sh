#!/bin/sh
set -eu
export SMS_DOMAIN API_DOMAIN PORTAL_DOMAIN ADMIN_DOMAIN
if [ -s "/etc/letsencrypt/live/$SMS_DOMAIN/fullchain.pem" ] && [ -s "/etc/letsencrypt/live/$SMS_DOMAIN/privkey.pem" ]; then
  envsubst '${SMS_DOMAIN} ${API_DOMAIN} ${PORTAL_DOMAIN} ${ADMIN_DOMAIN}' < /opt/telnexa/tls.conf.template > /etc/nginx/conf.d/default.conf
else
  envsubst '${SMS_DOMAIN} ${API_DOMAIN} ${PORTAL_DOMAIN} ${ADMIN_DOMAIN}' < /opt/telnexa/http.conf.template > /etc/nginx/conf.d/default.conf
fi
