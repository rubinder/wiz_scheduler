"""The manager-side reservations gate: one FastAPI dependency shared by every
/reservations router, so a route test can check they all carry the same one."""

from fastapi import HTTPException

from backend.config import settings


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
