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
    STRIPE_BILLING_PORTAL_RETURN_URL: str = "http://localhost:5173/manager/schedule"
    STRIPE_WEBHOOK_SECRET: str = ""  # whsec_... from Stripe webhook config

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

    # Subscription cancellation lifecycle
    SUBSCRIPTION_GRACE_DAYS: int = 90
    SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE: int = 14

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

    # Forgot-password rate limiting.
    #
    # FORGOT_PASSWORD_COOLDOWN_MINUTES — per-email cooldown. Within this
    #     window, /auth/forgot-password skips minting a new token and
    #     sending a new email — still returns 204 either way (preserves
    #     no-leak). Targets the email-bombing-a-known-victim threat.
    # FORGOT_PASSWORD_RATE_LIMIT_PER_5MIN — per-source-IP rate limit
    #     enforced by an in-process sliding-window counter. AWS WAFv2's
    #     minimum is 100 req/5min, too loose for our threat. Tighter
    #     limits live at the app layer. Targets the cross-email
    #     enumeration / cost-bombing threat. Returns 429 to the attacker
    #     (does leak that the IP is rate-limited, but the IP-level signal
    #     was already public).
    FORGOT_PASSWORD_COOLDOWN_MINUTES: int = 5
    FORGOT_PASSWORD_RATE_LIMIT_PER_5MIN: int = 5

    # Additional abuse / cost-control rate limits (all share the in-memory
    # SlidingWindowLimiter; see backend/services/rate_limit.py for the
    # per-worker caveat).
    #
    # SCHEDULE_GENERATE_AI_BURST_PER_HOUR — per-Company burst cap on
    #     /schedules/generate in AI mode. Backstop against sequential abuse
    #     that the schedule_lock can't catch (trigger → wait release →
    #     trigger). Local-mode generation bypasses this counter.
    # LOGIN_RATE_LIMIT_PER_5MIN — per-source-IP cap on /auth/login. bcrypt
    #     at cost factor 12 burns ~250ms of CPU per attempt; an unthrottled
    #     attacker can pin an async worker. Also blocks credential stuffing.
    # REGISTER_RATE_LIMIT_PER_HOUR — per-source-IP cap on /auth/register.
    #     Each registration creates Company+User rows, a Stripe customer,
    #     and sends a Resend welcome email — bots are expensive to absorb.
    SCHEDULE_GENERATE_AI_BURST_PER_HOUR: int = 30
    LOGIN_RATE_LIMIT_PER_5MIN: int = 15
    REGISTER_RATE_LIMIT_PER_HOUR: int = 3

    # Per-OG daily Anthropic spend circuit breaker (Tier 2E).
    #
    # Backstop for runaway scenarios the credit system can't catch: a
    # mis-tracked cost (free tier never depletes), a pipeline retry loop,
    # or a customer with autoreload generating around the clock past any
    # reasonable monthly budget. Trips POST /schedules/generate (AI mode
    # only) with 402 daily_cost_cap_exceeded once the rolling-24h
    # Anthropic cost on the OG crosses this dollar threshold. Counter
    # source: token_usage_daily (one row per OG per day; AI mode never
    # bypasses the write path).
    OG_ANTHROPIC_DAILY_CAP_USD: float = 50.00

    # Quota guards for shared external resources.
    #
    # OG_EMAIL_DAILY_CAP — total emails Resend may send for one OG in a
    #     rolling 24h window. Above this, sender helpers short-circuit
    #     and log resend.skipped. Caller still gets the normal response
    #     (no leak about whether the email was actually sent). Protects
    #     against runaway loops + Resend reputation damage from spiky
    #     volume.
    # INTEGRATION_IMPORT_BURST — how many bulk imports the same OG may run
    #     for the same integration (7shifts, Deputy) inside one rolling
    #     cooldown window before the cooldown applies. The window is a
    #     sliding INTEGRATION_IMPORT_COOLDOWN_MINUTES; the Nth+1 import
    #     returns 429 import_cooldown until the oldest of the N ages out.
    # INTEGRATION_IMPORT_COOLDOWN_MINUTES — length of the rolling window
    #     over which INTEGRATION_IMPORT_BURST imports are counted. Once the
    #     burst is exhausted, retry_after counts down to the next free slot.
    # GDPR_EXPORT_COOLDOWN_MINUTES — minimum gap between two
    #     /gdpr/export calls by the same user. Returns 429
    #     export_cooldown above this.
    OG_EMAIL_DAILY_CAP: int = 200
    INTEGRATION_IMPORT_BURST: int = 10
    INTEGRATION_IMPORT_COOLDOWN_MINUTES: int = 10
    GDPR_EXPORT_COOLDOWN_MINUTES: int = 60

    # Schedule generation/approval temporal lock.
    #
    # The lock has no heartbeat — once acquired it sits until release() OR
    # until expires_at. The TTL must therefore cover the worst legitimate
    # wall-clock of the operation it gates, OR a second manager's request
    # will stale-sweep the still-running lock and a race condition kicks in.
    #
    # SCHEDULE_LOCK_TTL_SECONDS                — default for approve and any
    #     non-generate caller. Approve is sub-second, so 120s is a 100×+
    #     safety margin while keeping dead-browser recovery responsive.
    # SCHEDULE_LOCK_TTL_AI_GENERATE_SECONDS    — generate in AI mode. One
    #     Anthropic Claude call per location at ~30-80s typical. 80 covers
    #     the single-location p99; tune higher if monitoring shows
    #     multi-location runs hitting the cap.
    # SCHEDULE_LOCK_TTL_LOCAL_GENERATE_SECONDS — generate in local mode. Pure
    #     Python computation, usually completes well under 1s. 15s is a
    #     comfortable safety margin while keeping dead-browser recovery
    #     quick.
    SCHEDULE_LOCK_TTL_SECONDS: int = 120
    SCHEDULE_LOCK_TTL_AI_GENERATE_SECONDS: int = 80
    SCHEDULE_LOCK_TTL_LOCAL_GENERATE_SECONDS: int = 15

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
