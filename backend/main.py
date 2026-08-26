from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from backend.config import settings
from backend.logging_config import setup_logging
from backend.middleware.failure_logging import FailureLoggingMiddleware
from backend.middleware.metrics import MetricsMiddleware
from backend.routers import (
    affinities,
    auth,
    billing,
    check_ins,
    company,
    condensed_roles,
    employees,
    export_schedules,
    failure_logs,
    gdpr,
    import_7shifts,
    import_deputy,
    invites,
    locations,
    manager_invites,
    ownership_group,
    regions,
    roles,
    schedules,
    scheduling_preferences,
    shift_templates,
    special_hours,
    webhooks,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="WizScheduler API", version="1.0.0")

    # Metrics middleware (outermost — times the entire request including all other middleware)
    app.add_middleware(MetricsMiddleware)

    # Security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)

    # Failure logging middleware (must be added before CORS so it wraps the full request)
    app.add_middleware(FailureLoggingMiddleware)

    # CORS middleware — restrict origins via CORS_ORIGINS env var in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health + metrics endpoints (outside /api/v1 for infrastructure probes) ──

    @app.get("/health")
    async def health_check() -> Response:
        """Deep health check — verifies DB connectivity, pool stats, scheduling
        pipeline health. Returns 200 for healthy/degraded, 503 for unhealthy."""
        from backend.services.monitoring import get_system_health
        health = await get_system_health()
        status_code = 200 if health["status"] != "unhealthy" else 503
        return JSONResponse(content=health, status_code=status_code)

    @app.get("/health/live")
    async def liveness_check() -> dict[str, str]:
        """Minimal liveness probe — always returns 200 if the process is alive.
        Use this for ALB health checks if the deep check proves too slow."""
        return {"status": "alive"}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        """Prometheus metrics endpoint, aggregated across all workers."""
        from backend.middleware.metrics import get_metrics, CONTENT_TYPE_LATEST
        return Response(content=get_metrics(), media_type=CONTENT_TYPE_LATEST)

    # Register all routers under /api/v1
    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(company.router, prefix=api_prefix)
    app.include_router(ownership_group.router, prefix=api_prefix)
    app.include_router(regions.router, prefix=api_prefix)
    app.include_router(locations.router, prefix=api_prefix)
    app.include_router(roles.router, prefix=api_prefix)
    app.include_router(condensed_roles.router, prefix=api_prefix)
    app.include_router(employees.router, prefix=api_prefix)
    app.include_router(affinities.router, prefix=api_prefix)
    app.include_router(shift_templates.router, prefix=api_prefix)
    app.include_router(special_hours.router, prefix=api_prefix)
    app.include_router(check_ins.router, prefix=api_prefix)
    app.include_router(schedules.router, prefix=api_prefix)
    app.include_router(scheduling_preferences.router, prefix=api_prefix)
    app.include_router(import_7shifts.router, prefix=api_prefix)
    app.include_router(import_deputy.router, prefix=api_prefix)
    app.include_router(export_schedules.router, prefix=api_prefix)
    app.include_router(failure_logs.router, prefix=api_prefix)
    app.include_router(invites.router, prefix=api_prefix)
    app.include_router(manager_invites.router, prefix=api_prefix)
    app.include_router(gdpr.router, prefix=api_prefix)
    app.include_router(billing.router, prefix=api_prefix)
    app.include_router(webhooks.router, prefix=api_prefix)

    # ── Daily background task: storage snapshots ──
    async def _daily_storage_snapshot_loop() -> None:
        """Run storage snapshots once per day. First run happens shortly
        after startup, then repeats every 24 hours. Idempotent per day."""
        import asyncio
        import logging

        from backend.database import async_session_factory
        from backend.services.billing import record_storage_snapshots

        log = logging.getLogger("wizscheduler.storage_snapshot")

        # Wait 30 seconds after startup to let the DB settle
        await asyncio.sleep(30)

        while True:
            try:
                async with async_session_factory() as db:
                    summary = await record_storage_snapshots(db)
                log.info("Daily storage snapshot completed: %s", summary)
            except Exception as e:
                log.error("Storage snapshot failed: %s", e)

            # Sleep 24 hours
            await asyncio.sleep(24 * 60 * 60)

    # ── Weekly background task: monthly InvoiceItem reconciliation ──
    async def _weekly_monthly_billing_loop() -> None:
        """Reconcile monthly InvoiceItems for storage + employee overages.

        Runs every 7 days. Idempotent — re-runs in the same period either
        no-op or update the InvoiceItem amount to match latest usage.
        """
        import asyncio
        import logging

        from backend.database import async_session_factory
        from backend.services.billing import bill_monthly_overages_all

        log = logging.getLogger("wizscheduler.monthly_billing")

        # Wait 60 seconds after startup, then 7 days between runs.
        await asyncio.sleep(60)

        while True:
            try:
                async with async_session_factory() as db:
                    summary = await bill_monthly_overages_all(db)
                log.info("Weekly monthly billing run: %s", summary)
            except Exception as e:
                log.error("Weekly monthly billing failed: %s", e)

            await asyncio.sleep(7 * 24 * 60 * 60)

    # ── Daily background task: cancellation lifecycle (PR β) ──
    async def _daily_cancellation_lifecycle_loop() -> None:
        """Walk canceled OGs daily. Send reminder at day 76, hard-delete at day 90."""
        import asyncio
        import logging

        from backend.database import async_session_factory
        from backend.services.billing import process_cancellation_lifecycle

        log = logging.getLogger("wizscheduler.cancellation_lifecycle")

        # Wait 120 seconds after startup, then 24h between runs.
        await asyncio.sleep(120)

        while True:
            try:
                async with async_session_factory() as db:
                    result = await process_cancellation_lifecycle(db)
                log.info(
                    "Cancellation lifecycle pass: reminders=%s deletions=%s",
                    result.get("reminders_sent"),
                    result.get("deletions_done"),
                )
            except Exception as e:
                log.error("Cancellation lifecycle failed: %s", e, exc_info=True)

            await asyncio.sleep(24 * 60 * 60)

    @app.on_event("startup")
    async def _start_background_tasks() -> None:
        import asyncio
        from backend.services.monitoring import run_self_check_loop
        asyncio.create_task(_daily_storage_snapshot_loop())
        asyncio.create_task(_weekly_monthly_billing_loop())
        asyncio.create_task(_daily_cancellation_lifecycle_loop())
        asyncio.create_task(run_self_check_loop())

    # Load-test profile: bypass JWT signature verification.
    # The register endpoint still works normally (creates real users/companies),
    # but all authenticated endpoints skip crypto — they decode the JWT payload
    # without verifying the signature and look up the user directly.
    # Requires both LOAD_TEST=true and ENV != "production".
    if settings.LOAD_TEST:
        if settings.ENV == "production":
            raise RuntimeError("LOAD_TEST must not be enabled in production")

        import logging

        from fastapi import Depends
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from jose import jwt as jose_jwt
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from backend.database import async_session_factory
        from backend.dependencies import get_current_user, get_db, require_manager, get_ownership_group_company_ids
        from backend.models.company import Company
        from backend.models.user import User

        logger = logging.getLogger("wizscheduler.loadtest")
        logger.warning("LOAD_TEST mode active — JWT signature verification is DISABLED")

        _lt_security = HTTPBearer()

        async def _lt_get_current_user(
            credentials: HTTPAuthorizationCredentials = Depends(_lt_security),
            db: AsyncSession = Depends(get_db),
        ) -> User:
            """Decode JWT without verifying signature — load test only."""
            payload = jose_jwt.get_unverified_claims(credentials.credentials)
            user_id = payload.get("sub")
            if not user_id:
                from fastapi import HTTPException, status
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No sub in token")
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                from fastapi import HTTPException, status
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
            return user

        async def _lt_require_manager(
            current_user: User = Depends(_lt_get_current_user),
        ) -> User:
            return current_user  # skip role check in load test

        async def _lt_ownership_group_ids(
            current_user: User = Depends(_lt_get_current_user),
            db: AsyncSession = Depends(get_db),
        ) -> list[str]:
            result = await db.execute(
                select(Company.ownership_group_id).where(Company.id == current_user.company_id)
            )
            og_id = result.scalar_one_or_none()
            if og_id is None:
                return [current_user.company_id]
            result = await db.execute(
                select(Company.id).where(Company.ownership_group_id == og_id)
            )
            return list(result.scalars().all())

        app.dependency_overrides[get_current_user] = _lt_get_current_user
        app.dependency_overrides[require_manager] = _lt_require_manager
        app.dependency_overrides[get_ownership_group_company_ids] = _lt_ownership_group_ids

    # In production, serve the frontend static build
    if settings.ENV == "production":
        from pathlib import Path

        from fastapi.staticfiles import StaticFiles

        static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        if static_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
