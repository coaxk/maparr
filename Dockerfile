# ══════════════════════════════════════════════════════════════
# MapArr — Production Dockerfile
# Two-stage build: install deps → lean runtime image
# No Node build step — frontend is static HTML/CSS/JS
# ══════════════════════════════════════════════════════════════

# ── Stage 1: Install Python dependencies ─────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Production image ────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.title="MapArr" \
      org.opencontainers.image.version="1.5.0" \
      org.opencontainers.image.description="Path Mapping Problem Solver for Docker *arr apps"

# Install curl (healthcheck) and Docker CLI + compose plugin.
# Docker CLI is needed for `docker compose config` resolution.
# The full Docker daemon is NOT installed — we talk to the host
# daemon via the mounted socket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       ca-certificates \
       gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       docker-ce-cli \
       docker-compose-plugin \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=deps /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy application code (no tests in production image)
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

# Create stacks mount point and non-root user.
# The maparr user owns /app and /stacks. For Apply Fix (writing corrected YAML),
# the stacks volume must be mounted without :ro.
RUN mkdir -p /stacks \
    && useradd -r -s /bin/false maparr \
    && chown -R maparr:maparr /app /stacks

USER maparr

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAPARR_STACKS_PATH=/stacks

EXPOSE 9494

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9494/api/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "9494", "--log-level", "info"]
