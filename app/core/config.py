from functools import lru_cache
from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # -------------------------
    # App
    # -------------------------
    app_env: str = "dev"
    app_port: int = 8000

    # -------------------------
    # Database
    # -------------------------
    # Accepts either a plain Postgres URL or SQLAlchemy-style URL.
    # Examples:
    # - postgresql://user:pass@host:5432/db
    # - postgres://user:pass@host:5432/db
    # - postgresql+asyncpg://user:pass@host:5432/db
    # - postgresql+psycopg://user:pass@host:5432/db
    database_url: str | None = None
    postgres_url: str | None = None
    async_postgres_url: str | None = None
    sync_postgres_url: str | None = None

    # -------------------------
    # External Services
    # -------------------------
    redis_url: str
    qdrant_url: str
    rabbitmq_url: str

    # -------------------------
    # SMTP / Notifications
    # -------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "Knowledge AI"
    smtp_subject_prefix: str = "Knowledge AI"
    smtp_app_name: str = "Knowledge AI"
    smtp_use_ssl: bool = False
    smtp_starttls: bool = True
    smtp_require_auth: bool = True
    smtp_timeout_seconds: int = 15

    # -------------------------
    # AI Providers
    # -------------------------
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    use_langgraph_chat: bool = False

    # -------------------------
    # Support Agent
    # -------------------------
    support_policy_path: str | None = None
    support_kb_user_fallback: str = "__support__"

    # -------------------------
    # Voice Providers
    # -------------------------
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
    twilio_public_base_url: str | None = None
    default_voice_agent_id: str = "general"
    voice_stt_provider: str = "auto"  # auto|gemini|whisper|stub
    voice_stt_timeout_seconds: int = 20
    voice_stt_gemini_model: str = "gemini-2.0-flash"
    whisper_api_key: str | None = None
    whisper_base_url: str = "https://api.openai.com/v1"
    whisper_model: str = "whisper-1"
    voice_tts_provider: str = "auto"  # auto|elevenlabs|openai|azure|stub
    voice_tts_timeout_seconds: int = 20
    elevenlabs_api_key: str | None = None
    elevenlabs_base_url: str = "https://api.elevenlabs.io/v1"
    voice_tts_elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    voice_tts_elevenlabs_model_id: str = "eleven_turbo_v2_5"
    voice_tts_elevenlabs_output_format: str = "mp3_44100_128"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    voice_tts_openai_model: str = "gpt-4o-mini-tts"
    voice_tts_openai_voice: str = "alloy"
    voice_tts_openai_format: str = "mp3"
    azure_speech_key: str | None = None
    azure_speech_region: str | None = None
    voice_tts_azure_voice_name: str = "en-US-JennyNeural"
    voice_tts_azure_output_format: str = "audio-24khz-48kbitrate-mono-mp3"

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

    @model_validator(mode="after")
    def _resolve_database_urls(self) -> "Settings":
        base_url = self._normalize_postgres_scheme(
            (self.database_url or self.postgres_url or "").strip()
        )
        async_url = self._normalize_postgres_scheme((self.async_postgres_url or "").strip())
        sync_url = self._normalize_postgres_scheme((self.sync_postgres_url or "").strip())

        if not async_url and not sync_url and not base_url:
            raise ValueError(
                "Database URL is required. Provide DATABASE_URL (or POSTGRES_URL), "
                "or set ASYNC_POSTGRES_URL and SYNC_POSTGRES_URL."
            )

        if not async_url and base_url:
            async_url = self._to_async_postgres_url(base_url)
        if not sync_url and base_url:
            sync_url = self._to_sync_postgres_url(base_url)

        if async_url and not sync_url:
            sync_url = self._to_sync_postgres_url(async_url)
        if sync_url and not async_url:
            async_url = self._to_async_postgres_url(sync_url)

        self.async_postgres_url = async_url
        self.sync_postgres_url = sync_url
        return self

    @staticmethod
    def _normalize_postgres_scheme(url: str) -> str:
        if url.startswith("postgres://"):
            return "postgresql://" + url[len("postgres://") :]
        return url

    @staticmethod
    def _to_async_postgres_url(url: str) -> str:
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql+psycopg://"):
            return "postgresql+asyncpg://" + url[len("postgresql+psycopg://") :]
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://") :]
        return url

    @staticmethod
    def _to_sync_postgres_url(url: str) -> str:
        if url.startswith("postgresql+psycopg://"):
            return url
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
