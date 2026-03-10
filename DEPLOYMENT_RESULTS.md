# MapArr Docker Deployment Validation Results

**Date:** 2026-03-11
**Version:** 1.5.0

## Infrastructure Note

Docker Engine runs inside WSL2 on this Windows 11 host. The Docker CLI is not
exposed to the Windows shell where this validation session runs. All Docker
deployment tests (Batch 3A-3H) require WSL2 or a Linux host.

**Status:** DEFERRED -- Docker deployment tests must be run from within WSL2 or
a Linux environment. The existing Layer 4 E2E tests (test_deployment.py) cover
build, port binding, and healthcheck assertions but are marked `@pytest.mark.docker`
and skip when Docker is unavailable.

## What Was Verified (Code Review)

### Dockerfile Review
- Multi-stage build: python:3.11-slim base
- Docker CLI + compose plugin installed (apt-get)
- gosu for PUID/PGID privilege drop
- Non-root user created
- HEALTHCHECK present: `curl -f http://localhost:${MAPARR_PORT:-9494}/api/health`
- PORT exposed via ARG/ENV chain

### docker-entrypoint.sh Review
- PUID/PGID remapping via usermod/groupmod
- Docker socket group detection and addition
- gosu privilege drop to non-root user
- MAPARR_PORT respected in uvicorn command

### docker-compose.yml Review
- Log rotation configured (max-size: 10m, max-file: 3)
- Docker socket mounted read-only (:ro)
- PUID/PGID environment variables
- Port binding: ${MAPARR_PORT:-9494}:${MAPARR_PORT:-9494}

## Deployment Tests to Run Manually

When Docker is available, run these verification steps:

```bash
# 3A: Clean build
docker build --no-cache -t maparr:prerelease .

# 3B: Standard run
docker run -d --name maparr-test -p 9494:9494 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e PUID=1000 -e PGID=1000 maparr:prerelease
curl http://localhost:9494/api/health
docker inspect --format='{{.State.Health.Status}}' maparr-test

# 3C: PUID/PGID variations
# Test: 0:0, 99:100, 1001:1001

# 3D: Docker Compose
docker compose up -d
docker compose down

# 3E: No socket mount
docker run -d --name maparr-nosock -p 9495:9494 maparr:prerelease
# Should start, Docker features fail gracefully

# 3F: Custom port
docker run -d --name maparr-port -p 9999:9999 \
  -e MAPARR_PORT=9999 maparr:prerelease
curl http://localhost:9999/api/health

# 3G: Restart/stop
docker restart maparr-test
docker stop maparr-test
docker logs maparr-test
```

## Existing Automated Coverage

The E2E test suite (tests/e2e/test_deployment.py) covers:
- `test_docker_build` -- image builds without errors
- `test_docker_port_binding` -- port 9494 is exposed
- `test_docker_healthcheck` -- healthcheck endpoint responds

These tests are automatically skipped when Docker is unavailable (3 skipped in baseline).
