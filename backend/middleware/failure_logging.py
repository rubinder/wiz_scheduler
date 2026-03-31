import traceback

from fastapi import Request, Response
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from backend.config import settings
from backend.services.failure_logger import log_failure


def _extract_company_id(request: Request) -> str | None:
    """Best-effort extraction of company_id from the Authorization header."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("company_id")
    except Exception:
        return None


class FailureLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception as exc:
            # Unhandled exception — log and re-raise
            company_id = _extract_company_id(request)
            await log_failure(
                category="UNHANDLED",
                source=f"{request.method} {request.url.path}",
                message=str(exc),
                detail={
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query),
                    "traceback": traceback.format_exc(),
                },
                company_id=company_id,
            )
            raise

        if response.status_code >= 500:
            company_id = _extract_company_id(request)
            await log_failure(
                category="HTTP",
                source=f"{request.method} {request.url.path}",
                message=f"HTTP {response.status_code}",
                detail={
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query),
                    "status_code": response.status_code,
                },
                company_id=company_id,
            )

        return response
