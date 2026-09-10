ARG PYTHON_IMAGE=python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

FROM ${PYTHON_IMAGE} AS builder
ARG JASMIN_SOURCE_SHA=0aac58e466d583d0f0436df7b8afa3dc96191263
WORKDIR /build
ADD --checksum=sha256:3024b111c93ecbf0b1873f0803cd00e8791354d2ac79bff65d071cb1361f574a \
    https://github.com/jookies/jasmin/archive/0aac58e466d583d0f0436df7b8afa3dc96191263.tar.gz \
    /tmp/jasmin-source.tar.gz
RUN test "$JASMIN_SOURCE_SHA" = "0aac58e466d583d0f0436df7b8afa3dc96191263" \
    && mkdir /build/jasmin \
    && tar -xzf /tmp/jasmin-source.tar.gz --strip-components=1 -C /build/jasmin \
    && rm -f /tmp/jasmin-source.tar.gz \
    && sed -i \
       -e 's/cryptography~=43.0.0/cryptography==50.0.1/g' \
       -e 's/pyopenssl~=24.2.0/pyopenssl==26.4.0/g' \
       -e 's/pyasn1~=0.6.1/pyasn1==0.6.4/g' \
       -e 's/Twisted~=23.10.0/Twisted==26.4.0rc2/g' \
       /build/jasmin/pyproject.toml /build/jasmin/requirements.txt
COPY docker/jasmin/requirements.lock /build/requirements.lock
RUN python -m venv /opt/jasmin-venv \
    && /opt/jasmin-venv/bin/pip install --no-deps --require-hashes -r /build/requirements.lock \
    && /opt/jasmin-venv/bin/pip install --no-deps --no-build-isolation /build/jasmin \
    && sed -i 's/Requires-Dist: Twisted~=23.10.0/Requires-Dist: Twisted>=23.10.0/g' \
       /opt/jasmin-venv/lib/python3.12/site-packages/smpp_twisted3-0.8.dist-info/METADATA \
       /opt/jasmin-venv/lib/python3.12/site-packages/txamqp3-0.9.4.dist-info/METADATA \
    && /opt/jasmin-venv/bin/pip check \
    && /opt/jasmin-venv/bin/pip uninstall -y pip setuptools wheel

FROM ${PYTHON_IMAGE}
ARG SOURCE_COMMIT_SHA
ARG JASMIN_SOURCE_SHA=0aac58e466d583d0f0436df7b8afa3dc96191263
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac \
    && test "$JASMIN_SOURCE_SHA" = "0aac58e466d583d0f0436df7b8afa3dc96191263"
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA" \
      org.opencontainers.image.base.name="https://github.com/jookies/jasmin" \
      org.opencontainers.image.base.revision="$JASMIN_SOURCE_SHA" \
      org.opencontainers.image.version="$SOURCE_COMMIT_SHA"

ENV PATH=/opt/jasmin-venv/bin:$PATH \
    ROOT_PATH=/ \
    CONFIG_PATH=/etc/jasmin \
    RESOURCE_PATH=/etc/jasmin/resource \
    STORE_PATH=/etc/jasmin/store \
    LOG_PATH=/var/log/jasmin \
    UNICODEMAP_JP=unicode-ascii

RUN apk add --no-cache --upgrade \
       apk-tools=3.0.8-r0 \
       ca-certificates=20260611-r0 \
       ca-certificates-bundle=20260611-r0 \
       jemalloc=5.3.0-r6 \
       libapk=3.0.8-r0 \
       libcrypto3=3.5.8-r0 \
       libuuid=2.42.3-r1 \
       libgcc=15.2.0-r5 \
       libssl3=3.5.8-r0 \
       libstdc++=15.2.0-r5 \
       sqlite-libs=3.53.4-r0 \
    && addgroup -g 10001 -S jasmin \
    && adduser -u 10001 -S -G jasmin -h /home/jasmin jasmin \
    && install -d -o jasmin -g jasmin -m 0750 \
       /home/jasmin /etc/jasmin/resource /etc/jasmin/store /var/log/jasmin
ENV LD_PRELOAD=/usr/lib/libjemalloc.so.2
COPY --from=builder /opt/jasmin-venv /opt/jasmin-venv
COPY --from=builder /build/jasmin/misc/config/*.cfg /etc/jasmin/
COPY --from=builder /build/jasmin/misc/config/resource /etc/jasmin/resource
COPY docker/jasmin/harden_durability.py /tmp/telnexa-harden-jasmin-durability.py
RUN python /tmp/telnexa-harden-jasmin-durability.py \
    && rm -f /tmp/telnexa-harden-jasmin-durability.py \
    && sed -i '/\[jcli\]/a bind=0.0.0.0' /etc/jasmin/jasmin.cfg \
    && cp -a /etc/jasmin /opt/telnexa-default-config \
    && chown -R jasmin:jasmin /etc/jasmin /opt/telnexa-default-config /var/log/jasmin
COPY docker/jasmin/configure.py /usr/local/bin/telnexa-configure
COPY docker/jasmin/bootstrap.py /usr/local/bin/telnexa-bootstrap
COPY docker/jasmin/jcli_batch.py /usr/local/bin/telnexa-jcli-batch
COPY docker/jasmin/migrate_topology.py /usr/local/bin/telnexa-migrate-topology
COPY docker/jasmin/entrypoint.sh /usr/local/bin/telnexa-entrypoint
RUN chmod 0755 /usr/local/bin/telnexa-configure /usr/local/bin/telnexa-bootstrap \
    /usr/local/bin/telnexa-jcli-batch /usr/local/bin/telnexa-migrate-topology \
    /usr/local/bin/telnexa-entrypoint

USER jasmin
EXPOSE 1401 2775 8990
ENTRYPOINT ["/usr/local/bin/telnexa-entrypoint"]
