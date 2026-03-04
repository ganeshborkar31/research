from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    # -------------------------
    # App
    # -------------------------
    app_env: str = "dev"
    app_port: int = 8000

    # -------------------------
    # Database
    # -------------------------
    async_postgres_url: str
    sync_postgres_url: str

    # -------------------------
    # External Services
    # -------------------------
    redis_url: str
    qdrant_url: str
    rabbitmq_url: str

    # -------------------------
    # AI Providers
    # -------------------------
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    # -------------------------
    # Voice Providers
    # -------------------------
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None

    # -------------------------
    # Auth / JWT
    # -------------------------
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 15
    refresh_token_exp_days: int = 30
    refresh_cookie_name: str = "refresh_token"
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: str = "lax"
    otp_exp_minutes: int = 10
    otp_length: int = 6

    # -------------------------
    # Pydantic Config
    # -------------------------
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
