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
    use_langgraph_chat: bool = False

    # -------------------------
    # Voice Providers
    # -------------------------
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
