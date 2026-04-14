"""Prometheus metrics middleware and /metrics endpoint helper.

Instruments every HTTP request with latency histograms and counters.
Designed for multi-worker (uvicorn --workers 4) via prometheus_client
multiprocess mode.
"""

import os
import re
import time

from backend.config import settings

# Must be set BEFORE importing prometheus_client
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", settings.PROMETHEUS_MULTIPROC_DIR)

from prometheus_client import (  # noqa: E402
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

# ── Metric definitions ──

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    labelnames=["method", "endpoint", "status_code"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status_code"],
)

REQUEST_ERRORS = Counter(
    "http_request_errors_total",
    "Total HTTP 5xx errors",
    labelnames=["method", "endpoint"],
)

SCHEDULING_RUNS = Counter(
    "scheduling_pipeline_runs_total",
    "Scheduling pipeline executions",
    labelnames=["status"],  # "success", "failure", "parse_error"
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out_connections",
    "Number of currently checked-out DB connections",
    multiprocess_mode="livesum",
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow_connections",
    "Number of overflow DB connections",
    multiprocess_mode="livesum",
)


# ── Path normalization to prevent label cardinality explosion ──

# Match 6-8 char alphanumeric IDs (short IDs) and standard UUIDs in paths
_SHORT_ID = re.compile(r"/[a-z0-9]{6,8}(?=/|$)", re.IGNORECASE)
_UUID = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)",
    re.IGNORECASE,
)

# Paths to skip instrumentation entirely
_SKIP_PATHS = frozenset({"/metrics", "/health", "/health/live"})


def _normalize_path(path: str) -> str:
    """Replace IDs in URL paths with {id} to keep Prometheus cardinality bounded."""
    path = _UUID.sub("/{id}", path)
    path = _SHORT_ID.sub("/{id}", path)
    return path


# ── Multiprocess /metrics output ──


def get_metrics() -> bytes:
    """Generate Prometheus metrics output aggregated across all workers."""
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry)


# ── Middleware ──


class MetricsMiddleware(BaseHTTPMiddleware):
    """Instrument HTTP requests with Prometheus counters and histograms."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in _SKIP_PATHS:
            return await call_next(request)

        method = request.method
        endpoint = _normalize_path(path)
        start = time.monotonic()

        try:
            response = await call_next(request)
        except Exception:
            # Unhandled exception — record as 500
            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint, status_code="500").observe(elapsed)
            REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code="500").inc()
            REQUEST_ERRORS.labels(method=method, endpoint=endpoint).inc()
            raise

        elapsed = time.monotonic() - start
        status = str(response.status_code)
        REQUEST_LATENCY.labels(method=method, endpoint=endpoint, status_code=status).observe(elapsed)
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status).inc()

        if response.status_code >= 500:
            REQUEST_ERRORS.labels(method=method, endpoint=endpoint).inc()

        # Update DB pool gauges opportunistically on each request
        try:
            from backend.database import engine as _engine

            DB_POOL_CHECKED_OUT.set(_engine.pool.checkedout())
            DB_POOL_OVERFLOW.set(_engine.pool.overflow())
        except Exception:
            pass

        return response
