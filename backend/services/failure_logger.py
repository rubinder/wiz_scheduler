import logging
from typing import Any

from backend.database import async_session_factory
from backend.models.failure_log import FailureLog

logger = logging.getLogger(__name__)


async def log_failure(
    *,
    category: str,
    severity: str = "error",
    source: str,
    message: str,
    detail: dict[str, Any] | None = None,
    company_id: str | None = None,
) -> None:
    """Persist a failure record using a standalone session.

    Uses its own session so the log is committed even if the caller's
    transaction rolls back.  Never raises — logging must not cause
    secondary failures.
    """
    try:
        async with async_session_factory() as db:
            entry = FailureLog(
                company_id=company_id,
                category=category,
                severity=severity,
                source=source,
                message=message,
                detail=detail,
            )
            db.add(entry)
            await db.commit()
    except Exception:
        logger.exception("Failed to persist failure log entry")


async def log_failure_batch(
    entries: list[dict[str, Any]],
) -> None:
    """Persist multiple failure records in a single transaction."""
    if not entries:
        return
    try:
        async with async_session_factory() as db:
            for entry_data in entries:
                entry = FailureLog(
                    company_id=entry_data.get("company_id"),
                    category=entry_data["category"],
                    severity=entry_data.get("severity", "error"),
                    source=entry_data["source"],
                    message=entry_data["message"],
                    detail=entry_data.get("detail"),
                )
                db.add(entry)
            await db.commit()
    except Exception:
        logger.exception("Failed to persist failure log batch")
