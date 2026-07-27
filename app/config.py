"""Environment-based configuration. Never hardcode secrets — everything comes from .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    DATABASE_URL: str = "sqlite:///./agentcare.db"

    SESSION_SECRET: str = "dev-secret-change-me"

    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    UPLOAD_DIR: str = "uploads"


settings = Settings()
