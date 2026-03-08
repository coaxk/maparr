#!/bin/sh
# ══════════════════════════════════════════════════════════════
# MapArr — Container Entrypoint
#
# Handles PUID/PGID remapping so bind-mount permissions "just work"
# on any host, regardless of the host user's UID/GID.
#
# How it works:
#   1. If PUID/PGID are set, create/modify the maparr user to match
#   2. Fix ownership on writable directories
#   3. Drop privileges and exec the application as that user
#
# If PUID/PGID are NOT set, runs as the default maparr user (UID 1000).
# This matches the LinuxServer.io pattern that *arr users expect.
# ══════════════════════════════════════════════════════════════

set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Detect current maparr user/group IDs
CURRENT_UID=$(id -u maparr 2>/dev/null || echo "1000")
CURRENT_GID=$(id -g maparr 2>/dev/null || echo "1000")

# Only modify if IDs differ from what's baked into the image
if [ "$PGID" != "$CURRENT_GID" ]; then
    # Modify existing group or create new one
    if getent group maparr >/dev/null 2>&1; then
        groupmod -o -g "$PGID" maparr
    else
        groupadd -o -g "$PGID" maparr
    fi
fi

if [ "$PUID" != "$CURRENT_UID" ]; then
    usermod -o -u "$PUID" maparr
fi

echo "──────────────────────────────────────"
echo " MapArr v1.5.0"
echo " Running as UID: $(id -u maparr) GID: $(id -g maparr)"
echo "──────────────────────────────────────"

# Fix ownership on the app directory so the remapped user can write
# pid files, __pycache__, etc. The /stacks mount is typically :ro
# so we don't touch it — Apply Fix users mount without :ro and must
# ensure host permissions allow writes.
chown -R maparr:maparr /app

# If DOCKER_HOST is set (socket proxy), no socket permissions to fix.
# If using socket mount, the maparr user needs read access to the socket.
if [ -S /var/run/docker.sock ]; then
    # Add maparr to the docker group (socket's group) so docker CLI works.
    # Get the GID of the socket and create/use a matching group.
    SOCK_GID=$(stat -c '%g' /var/run/docker.sock)
    if [ "$SOCK_GID" != "0" ]; then
        # Create a group matching the socket's GID if it doesn't exist
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -o -g "$SOCK_GID" dockersock
        fi
        SOCK_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
        usermod -aG "$SOCK_GROUP" maparr
    fi
fi

# Resolve the port — default 9494, overridable via MAPARR_PORT
PORT="${MAPARR_PORT:-9494}"

# Drop to maparr user and exec uvicorn
exec gosu maparr uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --log-level "${LOG_LEVEL:-info}"
