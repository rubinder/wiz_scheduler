"""System health checks and periodic self-monitoring.

Provides:
- Deep health checks (DB connectivity + pool stats, scheduling pipeline)
- Aggregated system health for the /health endpoint
- Background self-check loop that logs warnings/errors for alerting
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session_factory, engine
from backend.models.failure_log import FailureLog

logger = logging.getLogger("wizscheduler.monitoring")


async def check_db_health() -> dict[str, Any]:
    """Verify database connectivity and report connection pool stats."""
    result: dict[str, Any] = {
        "status": "unhealthy",
        "latency_ms": None,
        "pool": {},
        "error": None,
    }

    # Pool stats (synchronous, safe to call anytime)
    try:
        pool = engine.pool
        result["pool"] = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        result["pool"] = {"error": str(e)}

    # Connectivity check with timeout
    try:
        start = time.monotonic()
        async with asyncio.timeout(2):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        result["status"] = "healthy"
        result["latency_ms"] = elapsed_ms
    except asyncio.TimeoutError:
        result["error"] = "DB query timed out (>2s)"
    except Exception as e:
        result["error"] = str(e)

    return result


async def check_scheduling_health() -> dict[str, Any]:
    """Check for recent scheduling failures via the failure_logs table."""
    window = settings.MONITORING_SCHEDULING_FAILURE_WINDOW_MINUTES
    result: dict[str, Any] = {
        "status": "healthy",
        "recent_failures": 0,
        "affected_companies": 0,
        "window_minutes": window,
    }

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window)
        async with async_session_factory() as db:
            # Count failures
            count_result = await db.execute(
                select(func.count(FailureLog.id)).where(
                    FailureLog.category.in_(["SCHEDULING", "PIPELINE", "UNHANDLED"]),
                    FailureLog.created_at >= cutoff,
                )
            )
            failure_count = count_result.scalar() or 0

            # Count distinct affected companies
            company_result = await db.execute(
                select(func.count(func.distinct(FailureLog.company_id))).where(
                    FailureLog.category.in_(["SCHEDULING", "PIPELINE", "UNHANDLED"]),
                    FailureLog.created_at >= cutoff,
                    FailureLog.company_id.isnot(None),
                )
            )
            affected = company_result.scalar() or 0

        result["recent_failures"] = failure_count
        result["affected_companies"] = affected
        if failure_count > 0:
            result["status"] = "degraded"

    except Exception as e:
        result["status"] = "unhealthy"
        result["error"] = str(e)

    return result


async def get_system_health() -> dict[str, Any]:
    """Aggregate all health checks into a single response."""
    db_health, sched_health = await asyncio.gather(
        check_db_health(),
        check_scheduling_health(),
    )

    # Overall status: unhealthy if DB down, degraded if scheduling issues
    if db_health["status"] == "unhealthy":
        overall = "unhealthy"
    elif sched_health["status"] in ("unhealthy", "degraded"):
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": db_health,
            "scheduling": sched_health,
        },
    }


async def run_self_check_loop() -> None:
    """Background loop that periodically checks system health and logs results.

    Runs every MONITORING_INTERVAL_SECONDS. Logs at ERROR level when unhealthy,
    WARNING when degraded, INFO when healthy — enabling CloudWatch/log-based alerting.
    """
    # Let startup settle
    await asyncio.sleep(60)

    while True:
        try:
            health = await get_system_health()
            status = health["status"]

            if status == "unhealthy":
                logger.error("System health check: UNHEALTHY — %s", health)
            elif status == "degraded":
                logger.warning("System health check: DEGRADED — %s", health)
            else:
                logger.info("System health check: healthy")
        except Exception as e:
            logger.error("Self-check loop error: %s", e)

        await asyncio.sleep(settings.MONITORING_INTERVAL_SECONDS)
