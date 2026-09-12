from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:////tmp/telnexa-billing.db"
    jwt_secret: str = "development-only-change-me"
    middleware_url: str = "https://middleware.internal.codestra.agency/api/v1/events/telnexa"
    middleware_token_url: str = (
        "https://auth.codestra.co/realms/codestra/protocol/openid-connect/token"
    )
    middleware_client_id: str = "telnexa-gateway"
    middleware_client_secret_file: str = "/run/secrets/middleware_client_secret"
    middleware_hmac_secret_file: str = "/run/secrets/middleware_event_hmac"
    middleware_ca_file: str = "/run/secrets/middleware_ca"
    middleware_client_cert_file: str = "/run/secrets/middleware_client_cert"
    middleware_client_key_file: str = "/run/secrets/middleware_client_key"
    simulator_enabled: bool = True
    secure_cookies: bool = True
    model_config = SettingsConfigDict(env_prefix="BILLING_", case_sensitive=False)


settings = Settings()
