import logging
import warnings
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

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
    CORS_ORIGINS: str = "*"  # comma-separated list, e.g. "https://app.example.com,https://admin.example.com"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40

    # Load testing — disables auth; NEVER enable in production
    LOAD_TEST: bool = False

    # Google SSO
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_PRICE_ID: str = ""  # Price ID for the base subscription
    STRIPE_SUCCESS_URL: str = "http://localhost:5173/register?session_id={CHECKOUT_SESSION_ID}"
    STRIPE_CANCEL_URL: str = "http://localhost:5173/register"

    # LLM billing
    LLM_FREE_TIER_USD: float = 2.00          # free credits per ownership group per month
    LLM_OVERAGE_MARKUP: float = 1.30         # 130% of cost after free tier exhausted
    LLM_INPUT_COST_PER_M: float = 3.00       # $ per 1M input tokens (Claude Sonnet)
    LLM_OUTPUT_COST_PER_M: float = 15.00     # $ per 1M output tokens (Claude Sonnet)

    # Storage billing
    STORAGE_FREE_GB: float = 0.5             # free storage per ownership group
    STORAGE_COST_PER_GB: float = 0.50        # $/GB/month after free tier

    # Employee billing
    EMPLOYEE_FREE_TIER: int = 1_000           # free employees per ownership group
    EMPLOYEE_COST_PER_BLOCK: float = 1.00    # $ per 1k employees after free tier
    EMPLOYEE_BLOCK_SIZE: int = 1_000         # employees per billing block

    # Schedule generation billing
    SCHEDULE_FREE_TIER: int = 50             # free schedules per ownership group per month
    SCHEDULE_COST_PER_BLOCK: float = 0.10    # $ per 50 schedules after free tier
    SCHEDULE_BLOCK_SIZE: int = 50            # schedules per billing block

    # Base subscription
    BASE_MONTHLY_USD: float = 18.00          # baseline monthly charge

    # Auto-reload (real-time billing for AI + schedules)
    AUTORELOAD_DEFAULT_ENABLED: bool = True
    AUTORELOAD_DEFAULT_THRESHOLD_USD: float = 2.0
    AUTORELOAD_DEFAULT_AMOUNT_USD: float = 10.0

    # Data retention periods (days)
    RETENTION_REJECTED_SCHEDULES_DAYS: int = 30
    RETENTION_STALE_DRAFTS_DAYS: int = 7
    RETENTION_OLD_AVAILABILITY_DAYS: int = 90
    RETENTION_FAILURE_LOGS_DAYS: int = 90
    RETENTION_EXPIRED_INVITES_DAYS: int = 30
    RETENTION_REVOKED_CONSENTS_DAYS: int = 365

    # Monitoring
    MONITORING_INTERVAL_SECONDS: int = 300                   # self-check loop interval
    MONITORING_SCHEDULING_FAILURE_WINDOW_MINUTES: int = 30   # look-back for recent failures
    PROMETHEUS_MULTIPROC_DIR: str = "/tmp/prometheus_multiproc"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()

if settings.ENV == "production" and settings.SECRET_KEY == "change-me-in-production":
    warnings.warn(
        "SECRET_KEY is set to the default value in production! "
        "Set a strong random SECRET_KEY via environment variable or .env file.",
        stacklevel=1,
    )
