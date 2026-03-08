# ══════════════════════════════════════════════════════════════
# MapArr v1.5.0 — Production Dockerfile
#
# Required volume mount:
#   -v /path/to/stacks:/stacks:ro
#
# Optional — enables `docker compose config` for full resolution:
#   -v /var/run/docker.sock:/var/run/docker.sock:ro
#
# Socket proxy users (Tecnativa, LinuxServer, etc.):
#   -e DOCKER_HOST=tcp://socket-proxy:2375
#   No socket mount needed — MapArr respects DOCKER_HOST.
#   If your proxy blocks compose endpoints, MapArr falls back
#   to manual YAML parsing automatically.
#
# PUID/PGID:
#   -e PUID=1000 -e PGID=1000
#   Matches LinuxServer.io convention. Defaults to 1000:1000.
#   Set these to match your host user so volume permissions work.
#
# Port:
#   -e MAPARR_PORT=9494
#   Change the internal port if needed (default: 9494).
# ══════════════════════════════════════════════════════════════

FROM python:3.11-slim
LABEL org.opencontainers.image.title="MapArr"
LABEL org.opencontainers.image.version="1.5.0"
LABEL org.opencontainers.image.description="Path mapping intelligence for Docker *arr applications"

# Install:
#   - Docker CLI + compose plugin (for `docker compose config`)
#   - gosu (for PUID/PGID privilege drop — same as LinuxServer.io's s6)
#   - curl (for healthcheck)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg gosu \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian \
        $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code — frontend is vanilla JS, no build step needed
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

RUN touch /app/backend/__init__.py

# Create default maparr user (UID/GID 1000).
# The entrypoint script will remap to PUID/PGID at runtime.
RUN groupadd -g 1000 maparr \
    && useradd -u 1000 -g maparr -d /app -s /bin/sh maparr \
    && chown -R maparr:maparr /app

# Entrypoint handles PUID/PGID remapping then drops to maparr user
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV MAPARR_STACKS_PATH=/stacks \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 9494

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${MAPARR_PORT:-9494}/api/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
