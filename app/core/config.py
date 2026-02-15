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

