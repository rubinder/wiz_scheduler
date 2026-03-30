from pathlib import Path

from pydantic_settings import BaseSettings

# Resolve .env from the repo root (one level above this file's directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://shiftsync:shiftsync@localhost:5432/shiftsync"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ANTHROPIC_API_KEY: str = ""
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "noreply@shiftsync.example.com"
    ENV: str = "development"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
