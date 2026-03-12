![Version](https://img.shields.io/badge/version-1.5.0--beta-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-green)
![License](https://img.shields.io/badge/license-MIT-brightgreen)

# MapArr

**Path Mapping Problem Solver for Docker *arr apps**

MapArr scans your actual Docker Compose setup and tells you exactly what's wrong with your volume mounts. It doesn't just quote TRaSH Guides at you — it reads your real config, understands your full media pipeline, and generates specific fixes for your setup.

## What It Does

Most *arr app problems come down to path mapping. Sonarr mounts `/host/tv:/data/tv`, qBittorrent mounts `/host/downloads:/downloads`, and hardlinks silently fail because Docker sees them as separate filesystems. MapArr detects this automatically.

**Two modes:**

- **Fix Mode** — Paste an error from Sonarr/Radarr, MapArr identifies the service, matches it to the right stack, and shows you exactly what to change
- **Browse Mode** — Browse all your stacks, pick one, get a full analysis with mount intelligence

**Pipeline Intelligence:**

MapArr scans your entire root directory on boot and builds a unified map of all media services. When you analyze a single stack, it already knows about every other service — their roles, mount paths, and relationships. This isn't per-stack isolation. This is full directory awareness.

## Features

- **Pipeline-first analysis** — Scans 35+ compose files in under 1 second, builds a unified media service map
- **Role detection** — Automatically classifies services as *arr apps, download clients, or media servers
- **Mount consistency checking** — Verifies all media services share a common host mount (required for hardlinks)
- **Smart error matching** — Paste an error, MapArr figures out which stack caused it
- **Auto-apply fixes** — Apply corrected volume configuration directly to your compose file (with backup)
- **Mount intelligence** — Detects NFS, CIFS/SMB, WSL2, and local mounts with hardlink compatibility warnings
- **Category advisory** — Warns about the download client category trap that catches everyone
- **Quick-switch** — Type-to-search for instant stack switching without navigating back
- **RPM Wizard** — Guided Remote Path Mapping setup when mount restructuring isn't an option
- **Two-track solutions** — Quick Fix (RPM) or Proper Fix (restructure) for every conflict
- **Real-time logging** — Full log panel with SSE streaming, level filtering, and download
- **Diagnostic export** — One-click markdown export of your analysis for sharing/debugging
- **Update checker** — Checks GitHub releases for newer versions

## Screenshots

![Landing](screenshots/landing.png)
![Analysis](screenshots/after_click_start.png)

## Quick Start

### Docker (Recommended)

```bash
docker run -d \
  --name maparr \
  -p 9494:9494 \
  -v /path/to/your/stacks:/stacks:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/coaxk/maparr:latest
```

Then open `http://localhost:9494`.

### Docker Compose

```yaml
services:
  maparr:
    image: ghcr.io/coaxk/maparr:latest
    container_name: maparr
    restart: unless-stopped
    ports:
      - "9494:9494"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /path/to/your/stacks:/stacks:ro
    environment:
      MAPARR_STACKS_PATH: /stacks
```

### Bare Metal (Python 3.11+)

```bash
git clone https://github.com/coaxk/maparr.git
cd maparr
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 9494
```

## How It Works

1. **Discovery** — Scans your stacks directory for Docker Compose files (up to 3 levels deep)
2. **Pipeline Scan** — Identifies all media services, classifies their roles, maps their host mount paths
3. **Analysis** — Resolves the compose file, extracts volumes, detects conflicts between services
4. **Fix Generation** — Produces corrected YAML following the TRaSH Guides unified `/data` pattern
5. **Apply** — Optionally write the fix directly to your compose file (with `.bak` backup)

The analysis covers:
- **Separate mount trees** — the #1 cause of hardlink failure in *arr setups
- **Inconsistent host paths** — same container path backed by different host directories
- **Remote filesystems** — NFS/CIFS mounts where hardlinks can't work
- **Unreachable paths** — container paths with no backing volume mount
- **Pipeline mount conflicts** — services across different stacks using incompatible mount roots

## Volume Mounts

| Mount | Purpose | Required? |
|-------|---------|-----------|
| `/stacks:ro` | Your compose files directory | Yes |
| `/var/run/docker.sock:ro` | Docker socket for `docker compose config` resolution | Recommended |

The `:ro` flag ensures MapArr operates read-only during analysis. The auto-apply feature writes only when you explicitly confirm, and always creates a backup first.

**Docker socket security note:** The Docker socket grants full Docker API access. The `:ro` flag does NOT limit this — it's a Docker limitation. Only mount it if you trust MapArr. It runs read-only analysis — no containers are modified.

### Stacks Directory Examples

```bash
# Linux — stacks in /opt/docker
-v /opt/docker:/stacks:ro

# Linux — stacks in home directory
-v ~/docker:/stacks:ro

# Windows (Docker Desktop / WSL2)
-v C:\DockerContainers:/stacks:ro

# Komodo / Portainer / Dockge style (one stack per directory)
-v /home/user/stacks:/stacks:ro
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAPARR_PORT` | `9494` | Port to run on |
| `MAPARR_STACKS_PATH` | `/stacks` | Path to scan for compose files |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker socket path |
| `LOG_LEVEL` | `info` | Logging level (debug, info, warning, error) |

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

**"No stacks found"** — Make sure you're mounting the right directory. The stacks directory should contain subdirectories, each with a `docker-compose.yml` or `compose.yml`.

**"docker compose config failed"** — MapArr falls back to manual YAML parsing automatically. This is fine for most setups. If you need full resolution (extends, includes), ensure the Docker socket is mounted.

**"Analysis failed"** — Usually means the compose file has invalid YAML. Check syntax with `docker compose config` on the host.

## Architecture

- **Backend:** Python 3.11 + FastAPI (11 endpoints)
- **Frontend:** Vanilla HTML/CSS/JS (no framework, no build step)
- **Analysis Engine:** Volume mount classification, conflict detection, RPM calculation, TRaSH Guides pattern matching
- **Pipeline:** Full directory scanning with role detection, mount consistency checking across all stacks
- **Tests:** 426 tests covering RPM, pipeline, analysis, smart-match, cross-stack, and edge cases

## Security

MapArr handles filesystem paths and Docker socket access, so it enforces strict boundaries at every layer. Last audited: 2026-03-09.

- **Path traversal prevention** — All user-supplied paths are resolved via `Path.resolve()` and verified against the stacks root using `relative_to()`. Requests targeting paths outside the stacks directory are rejected before any read or write occurs.
- **Write operation boundary** — Apply Fix requires `MAPARR_STACKS_PATH` to be set (always true in Docker). Without an explicit boundary, write operations are refused. Change Path uses an allowlist when a stacks root is configured.
- **Compose filename whitelist** — The apply-fix endpoint only writes to files named `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, or `compose.yaml`. Arbitrary filenames are blocked.
- **System directory blocking** — Defense-in-depth denylist rejects system-critical directories (`/etc`, `/proc`, `/sys`, `/dev`, `/boot`, `/sbin`, `/root`, `/home`, `C:\Windows`, `C:\Program Files`).
- **XSS prevention** — The frontend renders all user-derived content via `textContent` assignments. Zero use of `innerHTML` with untrusted data across ~6500 lines of JS. All inline event handlers migrated to `addEventListener` for CSP readiness.
- **Safe YAML loading** — All YAML parsing uses `yaml.safe_load()`. No arbitrary Python object deserialization.
- **No shell injection** — Subprocess calls use list-form arguments (never `shell=True`). No user input is interpolated into commands.
- **Dependency hygiene** — All dependencies pinned to minimum safe versions. python-multipart CVE-2024-47874 patched. FastAPI, uvicorn, and PyYAML kept current.
- **Bounded resources** — SSE log queue capped at 100 entries per connection. Reconnection uses exponential backoff (5s→60s) to prevent self-inflicted DoS.
- **Read-only analysis** — No Docker containers are started, stopped, or modified. The auto-apply feature writes only to compose files, only when explicitly confirmed, and always creates a `.bak` backup first.
- **Non-root container** — Runs as PUID/PGID user (default 1000:1000), not root. Docker socket access via group membership, not privilege escalation.
- **No outbound connections** — MapArr makes zero external API calls. No telemetry, no update pings, no data leaves your machine. (The update checker compares a local version string against GitHub releases — initiated by the user, not automatic.)

## Supported Services

MapArr automatically detects and analyzes **27 media services** across three roles. These are the services that interact with media files on disk and need consistent volume mounts for hardlinks and imports to work.

### Arr Apps (10)

| Service | Purpose |
|---------|---------|
| **Sonarr** | TV series management |
| **Radarr** | Movie management |
| **Lidarr** | Music management |
| **Readarr** | Book & audiobook management |
| **Whisparr** | Adult content management |
| **Prowlarr** | Indexer manager |
| **Bazarr** | Subtitle management |
| **Mylar3** | Comic book management |
| **Kapowarr** | Comic book management (newer) |
| **LazyLibrarian** | Book, audiobook & magazine management |

### Download Clients (14)

| Service | Type |
|---------|------|
| **qBittorrent** | Torrent |
| **Transmission** | Torrent |
| **Deluge** | Torrent |
| **rTorrent** | Torrent |
| **Flood** | Torrent (web UI) |
| **Vuze** | Torrent |
| **SABnzbd** | Usenet |
| **NZBGet** | Usenet |
| **JDownloader** | Direct download |
| **aria2** | Multi-protocol |
| **pyLoad** | Direct download |
| **RDTClient** | Debrid (Real-Debrid) |
| **Decypharr** | Debrid blackhole |
| **Zurg** | Debrid WebDAV mount |

### Media Servers (3)

| Service | Purpose |
|---------|---------|
| **Plex** | Media server |
| **Jellyfin** | Media server |
| **Emby** | Media server |

Services not on this list (Tautulli, Overseerr, Portainer, etc.) still appear on the dashboard under **Other Stacks** for visibility, but aren't analyzed for path conflicts since they don't interact with media files directly.

> **Custom images?** Mount a `custom-images.json` file to add your own service definitions. See [QUICK_START.md](QUICK_START.md) for details.

## The *arr Ecosystem

MapArr is part of a 3-tool ecosystem for Docker media stack management:

| Tool | Purpose |
|------|---------|
| **MapArr** | Path mapping analysis and fixes |
| **ComposeArr** | Docker Compose hygiene linting (30 rules, health scoring) |
| **SubBrainArr** | Subtitle intelligence |

## Privacy

MapArr runs entirely locally. No telemetry. No external API calls. No data collection. Your compose files never leave your machine.

## License

MIT License. See [LICENSE](LICENSE) for details.

*arr app logos and names are trademarks of their respective projects. MapArr is an independent third-party tool — not affiliated with or endorsed by any *arr project.
