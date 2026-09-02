FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA" \
      org.opencontainers.image.version="$SOURCE_COMMIT_SHA"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOURCE_SHA=$SOURCE_COMMIT_SHA
WORKDIR /app
RUN apk upgrade --no-cache \
    && pip install --no-cache-dir --upgrade pip setuptools==84.0.0
RUN addgroup -S telnexa && adduser -S -G telnexa telnexa
COPY billing/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip uninstall -y pip setuptools
COPY --chown=telnexa:telnexa billing /app/billing
USER telnexa
EXPOSE 8000
CMD ["uvicorn","billing.app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
