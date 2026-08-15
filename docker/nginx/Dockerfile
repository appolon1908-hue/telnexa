FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY docker/nginx/http.conf.template /opt/telnexa/http.conf.template
COPY docker/nginx/tls.conf.template /opt/telnexa/tls.conf.template
COPY docker/nginx/entrypoint.sh /docker-entrypoint.d/05-telnexa-config.sh
RUN chmod 0755 /docker-entrypoint.d/05-telnexa-config.sh
