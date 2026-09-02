FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA" \
      org.opencontainers.image.version="$SOURCE_COMMIT_SHA"
RUN addgroup -S relay && adduser -S -G relay relay
COPY docker/webhook-relay/server.py /app/server.py
USER relay
EXPOSE 8080
ENTRYPOINT ["python", "/app/server.py"]
