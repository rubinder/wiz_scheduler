from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from backend.config import settings
from backend.logging_config import setup_logging
from backend.middleware.failure_logging import FailureLoggingMiddleware
from backend.routers import (
    affinities,
    auth,
    billing,
    company,
    condensed_roles,
    employees,
    export_schedules,
    failure_logs,
    gdpr,
    import_7shifts,
    invites,
    locations,
    ownership_group,
    regions,
    roles,
    schedules,
    shift_templates,
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

    # Health check endpoint (outside /api/v1 prefix for ALB/ECS probes)
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

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
    app.include_router(schedules.router, prefix=api_prefix)
    app.include_router(import_7shifts.router, prefix=api_prefix)
    app.include_router(export_schedules.router, prefix=api_prefix)
    app.include_router(failure_logs.router, prefix=api_prefix)
    app.include_router(invites.router, prefix=api_prefix)
    app.include_router(gdpr.router, prefix=api_prefix)
    app.include_router(billing.router, prefix=api_prefix)

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
