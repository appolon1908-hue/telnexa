ARG KEYCLOAK_BASE_IMAGE=quay.io/keycloak/keycloak:26.7.3@sha256:88943b6ad06d6293a239f0dfca5acec64218c9b3ab327bf9c936acf408a6ae3b

# Keycloak 26.7.3 carries Netty 4.1.136.Final. Overlay the two components
# affected by CVE-2026-75595/CVE-2026-75596 and CVE-2026-59903 with the
# upstream 4.1.137.Final fixes. Remote inputs are locked to Maven Central's
# published SHA-256 values and are verified again in the final image.
FROM scratch AS netty-security-fix
ADD --checksum=sha256:d0e4c6ee4779f59f6ab2fb5d388e4f57147c82270164b37945764bb9bda96a44 \
    https://repo.maven.apache.org/maven2/io/netty/netty-handler/4.1.137.Final/netty-handler-4.1.137.Final.jar \
    /io.netty.netty-handler-4.1.137.Final.jar
ADD --checksum=sha256:0535bb5a736472bef5c948d15eb273c4ab9f796656fc7c5d6b982ad92bddbd49 \
    https://repo.maven.apache.org/maven2/io/netty/netty-codec-http/4.1.137.Final/netty-codec-http-4.1.137.Final.jar \
    /io.netty.netty-codec-http-4.1.137.Final.jar

FROM ${KEYCLOAK_BASE_IMAGE} AS builder
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=true KC_HTTP_RELATIVE_PATH=/auth
RUN /opt/keycloak/bin/kc.sh build
USER 0
# Quarkus records the original dependency filenames during augmentation. Keep
# those paths stable while replacing their contents with the fixed artifacts.
COPY --from=netty-security-fix --chown=1000:0 --chmod=0644 /io.netty.netty-handler-4.1.137.Final.jar /opt/keycloak/lib/lib/main/io.netty.netty-handler-4.1.136.Final.jar
COPY --from=netty-security-fix --chown=1000:0 --chmod=0644 /io.netty.netty-codec-http-4.1.137.Final.jar /opt/keycloak/lib/lib/main/io.netty.netty-codec-http-4.1.136.Final.jar
USER 1000

FROM ${KEYCLOAK_BASE_IMAGE}
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA" \
      org.opencontainers.image.version="$SOURCE_COMMIT_SHA"
USER 0
COPY --from=builder /opt/keycloak/ /opt/keycloak/
RUN rm -f /opt/keycloak/lib/lib/main/com.microsoft.sqlserver.mssql-jdbc-*.jar \
    && test -z "$(find /opt/keycloak -type f -name 'com.microsoft.sqlserver.mssql-jdbc-*.jar' -print -quit)" \
    && printf '%s  %s\n%s  %s\n' \
      'd0e4c6ee4779f59f6ab2fb5d388e4f57147c82270164b37945764bb9bda96a44' \
      '/opt/keycloak/lib/lib/main/io.netty.netty-handler-4.1.136.Final.jar' \
      '0535bb5a736472bef5c948d15eb273c4ab9f796656fc7c5d6b982ad92bddbd49' \
      '/opt/keycloak/lib/lib/main/io.netty.netty-codec-http-4.1.136.Final.jar' \
      | sha256sum -c -
USER 1000
ENV KC_HTTP_RELATIVE_PATH=/auth
