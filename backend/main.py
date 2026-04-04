from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from backend.config import settings
from backend.logging_config import setup_logging
from backend.middleware.failure_logging import FailureLoggingMiddleware
from backend.routers import (
    affinities,
    auth,
    company,
    condensed_roles,
    employees,
    export_schedules,
    failure_logs,
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

    # In production, serve the frontend static build
    if settings.ENV == "production":
        from pathlib import Path

        from fastapi.staticfiles import StaticFiles

        static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        if static_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
