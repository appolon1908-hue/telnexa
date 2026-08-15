# Telnexa

Dockerized communications platform repository for **telnexa.co**.

Planned services:
- Jasmin SMS Gateway
- Redis
- RabbitMQ (if required by Jasmin version)
- Reverse proxy / TLS
- Health checks and monitoring

## Deployment target
This repository is intended for the Telnexa communications server. Do not commit secrets. Copy `.env.example` to `.env` on the server and fill real credentials there.

## Start
```bash
docker compose up -d
```
