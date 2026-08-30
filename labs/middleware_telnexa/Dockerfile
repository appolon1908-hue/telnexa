FROM python:3.13-slim@sha256:ffb752ab5136af9599b48f42a54be3cf173104d35c4200b98d329f2c9c7df50e

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
