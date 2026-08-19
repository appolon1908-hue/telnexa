ARG JASMIN_IMAGE=jookies/jasmin:0.11.0@sha256:3f049692d22fd66ab08a55073f79db96fe442473ede9615e8ac085ac505a1064
FROM ${JASMIN_IMAGE}
USER root
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
