# MapArr Deployment Security Guide

**Version:** 1.5.0
**Last Audit:** 2026-03-11

## Recommended Deployment Patterns

### 1. Internal Network Only (Recommended)
MapArr is designed for **single-user, local network use**. Run it on your Docker host
or NAS and access it from your local network only.

```yaml
ports:
  - "9494:9494"  # Bind to all interfaces on local network
```

### 2. Behind Reverse Proxy with Auth
If you need remote access, place MapArr behind a reverse proxy with authentication:
- **Authelia** or **Authentik** (SSO)
- **Nginx** with basic auth
- **Caddy** with forward_auth
- **Traefik** with middleware

```yaml
# Example: expose only on Docker network, let reverse proxy handle external access
ports: []  # No direct port binding
networks:
  - proxy_network
```

### 3. NOT Recommended: Direct Internet Exposure
MapArr has **no built-in authentication**. Do not expose port 9494 directly to the internet.

## What an Unauthenticated User Can Do

| Action | Endpoint | Risk |
|--------|----------|------|
| Read health/version info | GET /api/health | Low |
| List directory contents | POST /api/list-directories | Medium (information disclosure) |
| Scan filesystem for compose stacks | POST /api/pipeline-scan | Medium |
| Read and parse compose files | POST /api/analyze | Medium |
| **Write corrected YAML to compose files** | **POST /api/apply-fix[es]** | **High** |
| **Run docker compose commands** | **POST /api/redeploy** | **High** |
| View server logs | GET /api/logs | Low |

**Write operations require MAPARR_STACKS_PATH to be set** — this limits the blast radius
to the configured stacks directory only. Without it, apply-fix returns 403.

## Built-in Mitigations

| Protection | Status | Detail |
|------------|--------|--------|
| Rate limiting | Active | 10/min writes, 20/min analysis, 60/min reads |
| Path traversal prevention | Active | `relative_to()` boundary checks on all file operations |
| Compose filename allowlist | Active | Only docker-compose.yml/yaml, compose.yml/yaml |
| System directory blocklist | Active | /etc, /proc, /sys, /dev, /boot, /sbin, /root, /home blocked |
| YAML injection prevention | Active | `yaml.safe_load()` only — no unsafe deserialization |
| Command injection prevention | Active | List-form subprocess args, no `shell=True` |
| Subprocess timeouts | Active | 30s (compose config), 120s (compose up) |
| File backup before write | Active | `.bak` file created before any compose modification |
| XSS prevention | Active | All user content via `textContent`, zero `innerHTML` |
| SSE bounded queue | Active | maxsize=100, drops on overflow |

## Environment Variable Reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `MAPARR_STACKS_PATH` | Root directory for compose stacks (required for write ops) | None (scans /app/stacks in Docker) |
| `MAPARR_PORT` | Server listen port | 9494 |
| `PUID` | User ID for file ownership (Docker only) | 1000 |
| `PGID` | Group ID for file ownership (Docker only) | 1000 |
| `DOCKER_HOST` | Docker daemon endpoint (for socket proxy setups) | unix:///var/run/docker.sock |

## Security Audit History

| Date | Auditor | Finding | Status |
|------|---------|---------|--------|
| 2026-03-09 | Claude Opus 4.6 | 0 critical, 1 high (CVE-2024-47874), 3 medium | All fixed |
| 2026-03-11 | Claude Opus 4.6 | 0 critical, 2 high (input size limits), 1 medium (dir listing) | Documented |
