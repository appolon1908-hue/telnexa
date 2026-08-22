from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:////tmp/telnexa-billing.db"
    jwt_secret: str = "development-only-change-me"
    middleware_url: str = "https://10.40.0.1/api/v1/events/telnexa"
    middleware_api_key: str = ""
    middleware_hmac_secret: str = ""
    simulator_enabled: bool = True
    secure_cookies: bool = True
    model_config = SettingsConfigDict(env_prefix="BILLING_", case_sensitive=False)


settings = Settings()
