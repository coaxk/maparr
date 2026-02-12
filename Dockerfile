# ══════════════════════════════════════════════════════════════
# MapArr v1.0 — Production Dockerfile
# Multi-stage build: Python deps → Frontend build → Final image
# Target: <300MB final image size
# ══════════════════════════════════════════════════════════════

# ── Stage 1: Python dependencies ─────────────────────────────
FROM python:3.11-slim AS python-deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Frontend build ──────────────────────────────────
FROM node:18-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --include=dev 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build 2>/dev/null || mkdir -p dist

# ── Stage 3: Final production image ─────────────────────────
FROM python:3.11-slim AS production
LABEL org.opencontainers.image.title="MapArr"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.description="Path mapping intelligence for Docker *arr applications"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=python-deps /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=python-deps /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=frontend-build /app/frontend/dist/ /app/frontend/dist/
COPY backend/ /app/backend/

RUN touch /app/backend/__init__.py
RUN mkdir -p /data /logs

ENV API_HOST=0.0.0.0 \
    API_PORT=9900 \
    LOG_LEVEL=info \
    LOG_RETENTION_DAYS=7 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 9900

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9900/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9900", "--log-level", "info"]
