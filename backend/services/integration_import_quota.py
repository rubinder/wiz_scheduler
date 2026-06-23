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
    """Reserve an import slot, or raise 429 once the burst is exhausted.

    Up to ``INTEGRATION_IMPORT_BURST`` imports may run for the same
    (OG, integration) inside a rolling
    ``INTEGRATION_IMPORT_COOLDOWN_MINUTES`` window. The next import is
    blocked with 429 import_cooldown until the oldest of the burst ages
    out of the window, freeing a slot.

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
    burst = settings.INTEGRATION_IMPORT_BURST
    cutoff = now - cooldown
    recent = (await db.execute(
        select(IntegrationImport).where(
            IntegrationImport.ownership_group_id == ownership_group_id,
            IntegrationImport.integration == integration,
            IntegrationImport.started_at > cutoff,
        ).order_by(IntegrationImport.started_at.asc())
    )).scalars().all()
    if len(recent) >= burst:
        # Burst exhausted inside the window. A slot frees when the oldest
        # of the burst-filling imports ages out, so count retry_after from
        # that row's start. (If len > burst, index back so that once it
        # expires the remaining count drops below the burst.)
        oldest = recent[len(recent) - burst]
        # SQLite returns naive datetimes; PostgreSQL returns aware. Normalize
        # before doing arithmetic so the production+test math agrees.
        oldest_at = oldest.started_at
        if oldest_at.tzinfo is None:
            oldest_at = oldest_at.replace(tzinfo=timezone.utc)
        retry_after = int((oldest_at + cooldown - now).total_seconds())
        retry_after = max(retry_after, 1)
        logger.info(
            "integration_import.cooldown og=%s integration=%s count=%d/%d "
            "oldest_at=%s retry_after=%ds",
            ownership_group_id, integration, len(recent), burst,
            oldest_at.isoformat(), retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "import_cooldown",
                "message": (
                    f"You've run {len(recent)} {integration} imports in the "
                    f"last {settings.INTEGRATION_IMPORT_COOLDOWN_MINUTES} "
                    f"minutes (limit {burst}). Wait {retry_after}s and try "
                    f"again."
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
