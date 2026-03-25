from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routers import (
    affinities,
    auth,
    company,
    employees,
    export_schedules,
    import_7shifts,
    locations,
    ownership_group,
    regions,
    roles,
    schedules,
    shift_templates,
)


def create_app() -> FastAPI:
    app = FastAPI(title="WizScheduler API", version="1.0.0")

    # CORS middleware for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register all routers under /api/v1
    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix)
    app.include_router(company.router, prefix=api_prefix)
    app.include_router(ownership_group.router, prefix=api_prefix)
    app.include_router(regions.router, prefix=api_prefix)
    app.include_router(locations.router, prefix=api_prefix)
    app.include_router(roles.router, prefix=api_prefix)
    app.include_router(employees.router, prefix=api_prefix)
    app.include_router(affinities.router, prefix=api_prefix)
    app.include_router(shift_templates.router, prefix=api_prefix)
    app.include_router(schedules.router, prefix=api_prefix)
    app.include_router(import_7shifts.router, prefix=api_prefix)
    app.include_router(export_schedules.router, prefix=api_prefix)

    # In production, serve the frontend static build
    if settings.ENV == "production":
        from pathlib import Path

        from fastapi.staticfiles import StaticFiles

        static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        if static_dir.is_dir():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
