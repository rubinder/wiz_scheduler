# Backend runtime. The frontend ships from S3 behind CloudFront; the image
# never served it (main.py mounts frontend/dist, which the image did not have).
FROM python:3.11-slim

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Dependencies before source, so a code-only change reuses this layer.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install uv && uv pip install --system -r backend/requirements.txt
COPY backend/ ./backend

ENV PYTHONPATH=/app

# Switch to non-root user
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run migrations then start with multiple workers
# NOTE: seed.py removed from startup — run it manually as a one-time operation:
#   docker compose exec backend python -m backend.seed
CMD ["sh", "-c", "rm -rf /tmp/prometheus_multiproc && mkdir -p /tmp/prometheus_multiproc && cd backend && alembic upgrade head && cd /app && uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4"]
