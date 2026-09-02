ARG JASMIN_IMAGE=jookies/jasmin:0.11.0@sha256:3f049692d22fd66ab08a55073f79db96fe442473ede9615e8ac085ac505a1064
FROM ${JASMIN_IMAGE}
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA"

USER root
RUN cp -a /etc/jasmin /opt/telnexa-default-config
COPY docker/jasmin/harden_durability.py /tmp/telnexa-harden-jasmin-durability.py
RUN python /tmp/telnexa-harden-jasmin-durability.py \
    && rm -f /tmp/telnexa-harden-jasmin-durability.py
COPY docker/jasmin/configure.py /usr/local/bin/telnexa-configure
COPY docker/jasmin/bootstrap.py /usr/local/bin/telnexa-bootstrap
COPY docker/jasmin/jcli_batch.py /usr/local/bin/telnexa-jcli-batch
COPY docker/jasmin/entrypoint.sh /usr/local/bin/telnexa-entrypoint
RUN chmod 0755 /usr/local/bin/telnexa-configure /usr/local/bin/telnexa-bootstrap /usr/local/bin/telnexa-jcli-batch /usr/local/bin/telnexa-entrypoint

USER jasmin
ENTRYPOINT ["/usr/local/bin/telnexa-entrypoint"]
