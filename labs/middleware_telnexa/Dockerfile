FROM python:3.13-slim@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY billing/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && useradd --create-home --uid 10001 stage4

COPY billing /app/billing
COPY labs /app/labs

USER 10001:10001
CMD ["python", "-m", "labs.middleware_telnexa.certify"]
