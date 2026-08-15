ARG JASMIN_IMAGE=jookies/jasmin:0.11.0@sha256:3f049692d22fd66ab08a55073f79db96fe442473ede9615e8ac085ac505a1064
FROM ${JASMIN_IMAGE}

USER root
RUN cp -a /etc/jasmin /opt/telnexa-default-config
COPY docker/jasmin/configure.py /usr/local/bin/telnexa-configure
COPY docker/jasmin/bootstrap.py /usr/local/bin/telnexa-bootstrap
COPY docker/jasmin/jcli_batch.py /usr/local/bin/telnexa-jcli-batch
COPY docker/jasmin/entrypoint.sh /usr/local/bin/telnexa-entrypoint
RUN chmod 0755 /usr/local/bin/telnexa-configure /usr/local/bin/telnexa-bootstrap /usr/local/bin/telnexa-jcli-batch /usr/local/bin/telnexa-entrypoint

ENTRYPOINT ["/usr/local/bin/telnexa-entrypoint"]
