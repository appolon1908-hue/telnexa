FROM nginxinc/nginx-unprivileged:1.30.4-alpine@sha256:45ce1e2e699234253d1def7baa96218a5d00b498d1ba0cbb1a17b6bdf73d1351
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA"
COPY --chown=101:0 docker/nginx/http.conf.template /opt/telnexa/http.conf.template
COPY --chown=101:0 docker/nginx/tls.conf.template /opt/telnexa/tls.conf.template
COPY --chown=101:0 --chmod=0755 docker/nginx/entrypoint.sh /docker-entrypoint.d/05-telnexa-config.sh
USER 101:101
EXPOSE 8080 8443
