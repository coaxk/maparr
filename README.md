# MapArr

**Path Mapping Problem Solver for Docker *arr apps.**

You have a path error in Sonarr, Radarr, or your download client. You don't know which volume mount is wrong. MapArr analyzes your Docker Compose setup and tells you exactly what to change.

## Quick Start

```bash
docker run -d -p 9494:9494 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /path/to/your/stacks:/stacks:ro \
  maparr:latest
```

Open **http://localhost:9494** and paste your error.

## What It Solves

These errors:
- "Import failed, path does not exist: /data/tv/Show Name"
- "Permission denied" on import
- "Cross-device link" (hardlink failure)
- "Atomic move failed"

Are almost always caused by Docker volume mount misconfigurations. MapArr finds the problem and generates the fix.

## How It Works

1. **Paste your error** — MapArr extracts the service name, path, and error type
2. **Select your stack** — MapArr discovers compose files from your mounted directory
3. **Get the fix** — MapArr analyzes volumes, detects conflicts, and generates copy-pasteable YAML

The analysis covers:
- **Separate mount trees** — the #1 cause of hardlink failure in *arr setups
- **Inconsistent host paths** — same container path backed by different host directories
- **Remote filesystems** — NFS/CIFS mounts where hardlinks can't work
- **Unreachable paths** — container paths with no backing volume mount

## Volume Mounts

MapArr needs access to two things:

### Docker Socket (recommended)

```bash
-v /var/run/docker.sock:/var/run/docker.sock:ro
```

Lets MapArr run `docker compose config` for full variable resolution. Without it, MapArr falls back to manual YAML parsing with `.env` file substitution — works for most setups but won't resolve `extends` or `include` directives.

**Security note:** The Docker socket grants full Docker API access. The `:ro` flag does NOT limit this. Only mount it if you trust MapArr. It runs read-only analysis — no containers are modified.

### Stacks Directory (required)

```bash
-v /path/to/your/stacks:/stacks:ro
```

Point this to the parent directory containing your compose stacks. MapArr scans up to 3 levels deep for `docker-compose.yml` / `compose.yml` files.

**Examples:**

```bash
# Linux — stacks in /opt/docker
-v /opt/docker:/stacks:ro

# Linux — stacks in home directory
-v ~/docker:/stacks:ro

# Windows (Docker Desktop / WSL2)
-v C:\DockerContainers:/stacks:ro

# macOS
-v ~/docker-stacks:/stacks:ro
```

## Docker Compose

```yaml
services:
  maparr:
    image: maparr:latest
    container_name: maparr
    ports:
      - "9494:9494"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/your/stacks:/stacks:ro
    environment:
      MAPARR_STACKS_PATH: /stacks
    restart: unless-stopped
```

## Build Locally

```bash
git clone https://github.com/coaxk/maparr.git
cd maparr
docker compose up --build
```

Or run without Docker (Python 3.11+):

```bash
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 3000
```

## The TRaSH Guides Pattern

MapArr recommends the [TRaSH Guides](https://trash-guides.info/Hardlinks/Docker/) unified mount structure:

```
/host/data/
  media/
    tv/         <- Sonarr manages
    movies/     <- Radarr manages
    music/      <- Lidarr manages
  torrents/     <- Download client saves here
  usenet/       <- Usenet client saves here
```

All services mount the same parent: `/host/data:/data`. Subdirectories handle separation. Hardlinks work because everything is on one filesystem through one bind mount.

## Troubleshooting

**"No stacks found"** — Make sure you're mounting the right directory. The stacks directory should contain subdirectories, each with a `docker-compose.yml`.

**"docker compose config failed"** — MapArr falls back to manual YAML parsing automatically. This is fine for most setups. If you need full resolution (extends, includes), ensure the Docker socket is mounted.

**"Analysis failed"** — Usually means the compose file has invalid YAML. Check syntax with `docker compose config` on the host.

## Architecture

- **Backend:** Python 3.11 + FastAPI (5 endpoints, ~900 lines)
- **Frontend:** Static HTML/CSS/JS (no framework, no build step)
- **Analysis:** Pattern-based mount classification, conflict detection, fix generation
- **Tests:** 271 tests covering edge cases, stress conditions, and integration flows

## Privacy

MapArr runs entirely locally. No telemetry. No external API calls. Your compose files never leave your machine.

## License

MIT
