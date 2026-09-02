FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a
ARG SOURCE_COMMIT_SHA
RUN test "$(printf '%s' "$SOURCE_COMMIT_SHA" | wc -c)" -eq 40 \
    && case "$SOURCE_COMMIT_SHA" in *[!0-9a-f]*) exit 1 ;; *) : ;; esac
LABEL org.opencontainers.image.source="https://github.com/appolon1908-hue/telnexa" \
      org.opencontainers.image.revision="$SOURCE_COMMIT_SHA"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOURCE_SHA=$SOURCE_COMMIT_SHA
WORKDIR /app
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools==84.0.0
RUN addgroup --system telnexa && adduser --system --ingroup telnexa telnexa
COPY billing/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip uninstall -y pip setuptools
COPY --chown=telnexa:telnexa billing /app/billing
USER telnexa
EXPOSE 8000
CMD ["uvicorn","billing.app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
