"""Per-OG cooldown on bulk integration imports (#44)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.integration_import import IntegrationImport

logger = logging.getLogger(__name__)


async def begin_integration_import(
    db: AsyncSession,
    ownership_group_id: str | None,
    integration: str,
    location_id: str | None = None,
) -> IntegrationImport | None:
    """Reserve an import slot, or raise 429 if still inside the cooldown.

    Returns the newly-inserted IntegrationImport row so the caller can
    stamp ``finished_at`` once the import completes (purely for audit;
    the cooldown check uses started_at).

    Returns None when ``ownership_group_id`` is None — single-Company
    dev/test state with no OG to attribute the import to. In that case
    no audit row is written and the cooldown doesn't apply.

    The caller must commit (or the row vanishes on a downstream
    rollback, which is fine — the cooldown only counts persisted rows).
    """
    if ownership_group_id is None:
        return None

    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.INTEGRATION_IMPORT_COOLDOWN_MINUTES)
    cutoff = now - cooldown
    recent = (await db.execute(
        select(IntegrationImport).where(
            IntegrationImport.ownership_group_id == ownership_group_id,
            IntegrationImport.integration == integration,
            IntegrationImport.started_at > cutoff,
        ).order_by(IntegrationImport.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    if recent is not None:
        # SQLite returns naive datetimes; PostgreSQL returns aware. Normalize
        # before doing arithmetic so the production+test math agrees.
        recent_at = recent.started_at
        if recent_at.tzinfo is None:
            recent_at = recent_at.replace(tzinfo=timezone.utc)
        retry_after = int((recent_at + cooldown - now).total_seconds())
        retry_after = max(retry_after, 1)
        logger.info(
            "integration_import.cooldown og=%s integration=%s last_at=%s retry_after=%ds",
            ownership_group_id, integration,
            recent_at.isoformat(), retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "import_cooldown",
                "message": (
                    f"This integration import was run "
                    f"{int((now - recent_at).total_seconds())}s ago. "
                    f"Wait {retry_after}s and try again."
                ),
                "retry_after_seconds": retry_after,
            },
        )

    row = IntegrationImport(
        ownership_group_id=ownership_group_id,
        integration=integration,
        location_id=location_id,
        started_at=now,
    )
    db.add(row)
    # Commit immediately so a downstream rollback (e.g., the 7shifts API
    # returning 502) cannot erase the cooldown — otherwise a fast-failing
    # external path becomes a way to bypass the cap.
    await db.commit()
    return row
