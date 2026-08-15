FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
RUN addgroup -S relay && adduser -S -G relay relay
COPY docker/webhook-relay/server.py /app/server.py
USER relay
EXPOSE 8080
ENTRYPOINT ["python", "/app/server.py"]
