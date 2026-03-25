# Stage 1 — build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — backend runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=frontend-build /app/frontend/dist ./static
COPY backend/ ./backend
RUN pip install uv && uv pip install --system -r backend/requirements.txt
ENV PYTHONPATH=/app
CMD ["sh", "-c", "cd backend && alembic upgrade head && python seed.py && cd /app && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
