FROM quay.io/keycloak/keycloak:26.7.3@sha256:88943b6ad06d6293a239f0dfca5acec64218c9b3ab327bf9c936acf408a6ae3b AS builder
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=true KC_HTTP_RELATIVE_PATH=/auth
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.7.3@sha256:88943b6ad06d6293a239f0dfca5acec64218c9b3ab327bf9c936acf408a6ae3b
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA" \
      org.opencontainers.image.version="$SOURCE_COMMIT_SHA"
COPY --from=builder /opt/keycloak/ /opt/keycloak/
USER 0
RUN rm -f /opt/keycloak/lib/lib/main/com.microsoft.sqlserver.mssql-jdbc-*.jar \
    && test -z "$(find /opt/keycloak -type f -name 'com.microsoft.sqlserver.mssql-jdbc-*.jar' -print -quit)"
USER 1000
ENV KC_HTTP_RELATIVE_PATH=/auth
