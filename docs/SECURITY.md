# Billing security

PostgreSQL and worker networks are internal; only Nginx binds public ports. Public hosts use the existing Certbot volume and HSTS, CSP, frame, MIME and referrer headers. API keys are tenant-scoped, Argon2-hashed, displayed once, and revocable. Admin and login endpoints require rate limiting before activation. Webhooks must resolve against an approved HTTPS allowlist to prevent SSRF. Secrets are generated into mode-0600 `.env`, redacted from logs, and rotated independently. Tax fields are extensible and make no legal-compliance claim.
