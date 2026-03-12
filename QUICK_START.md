# MapArr v1.5 — Quick Start

## Prerequisites

- **Docker** v20.10+ with Compose v2
- Your Docker Compose stacks accessible on the filesystem

## Run with Docker Compose (recommended)

Copy the included `docker-compose.yml` and set your stacks path:

```bash
# Check your user ID (use these for PUID/PGID)
id
# uid=1000(youruser) gid=1000(youruser)

# Edit .env or export variables
export STACKS_PATH=/path/to/your/stacks
export PUID=1000
export PGID=1000

docker compose up -d
```

Open **http://localhost:9494** in your browser.

## Run with Docker CLI

### Basic (read-only scanning)
```bash
docker run -d \
  --name maparr \
  -p 9494:9494 \
  -e PUID=1000 \
  -e PGID=1000 \
  -v /path/to/your/stacks:/stacks:ro \
  ghcr.io/coaxk/maparr:latest
```

### With Docker Compose resolution (recommended)
Mounting the Docker socket lets MapArr use `docker compose config` for full
variable substitution, extends, and includes resolution.

```bash
docker run -d \
  --name maparr \
  -p 9494:9494 \
  -e PUID=1000 \
  -e PGID=1000 \
  -v /path/to/your/stacks:/stacks:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/coaxk/maparr:latest
```

### With Apply Fix (read-write stacks)
Remove `:ro` from the stacks mount to allow MapArr to write corrected compose files:
```bash
docker run -d \
  --name maparr \
  -p 9494:9494 \
  -e PUID=1000 \
  -e PGID=1000 \
  -v /path/to/your/stacks:/stacks \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/coaxk/maparr:latest
```

### With socket proxy
If you use a Docker socket proxy (Tecnativa, LinuxServer, etc.):
```bash
docker run -d \
  --name maparr \
  -p 9494:9494 \
  -e PUID=1000 \
  -e PGID=1000 \
  -e DOCKER_HOST=tcp://socket-proxy:2375 \
  -v /path/to/your/stacks:/stacks:ro \
  ghcr.io/coaxk/maparr:latest
```
No socket mount needed. MapArr only uses `docker compose config` — if your
proxy blocks it, MapArr falls back to manual YAML parsing automatically.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PUID` | `1000` | User ID to run as — match your host user (`id -u`) |
| `PGID` | `1000` | Group ID to run as — match your host group (`id -g`) |
| `MAPARR_PORT` | `9494` | Internal port (change if 9494 conflicts) |
| `MAPARR_STACKS_PATH` | `/stacks` | Container path to scan (set by volume mount) |
| `DOCKER_HOST` | (system) | Docker daemon endpoint — set for socket proxy |
| `LOG_LEVEL` | `info` | Log verbosity: `debug`, `info`, `warning`, `error` |

## Platform Guides

### Linux
Standard setup. Run `id` to get your PUID/PGID.
```bash
-e PUID=1000 -e PGID=1000
-v /var/run/docker.sock:/var/run/docker.sock:ro
```

### Unraid
Install via Community Applications or manually add the container:

1. **Docker tab** → Add Container
2. **Repository:** `ghcr.io/coaxk/maparr:latest`
3. **Network Type:** Bridge
4. **Port:** `9494` → `9494`
5. **Add Path:**
   - Container Path: `/stacks`
   - Host Path: `/mnt/user/appdata` (or wherever your compose files live)
   - Access Mode: Read Only (or Read/Write for Apply Fix)
6. **Add Path** (optional):
   - Container Path: `/var/run/docker.sock`
   - Host Path: `/var/run/docker.sock`
   - Access Mode: Read Only
7. **Add Variable:**
   - `PUID` = your Unraid user ID (usually `99` on Unraid)
   - `PGID` = `100` (users group on Unraid)

**Unraid PUID/PGID:** Unraid typically uses `PUID=99 PGID=100` (nobody:users).
Check your setup — if your *arr apps use different values, match those.

### Synology (DSM 7+)
1. **Container Manager** → Create → Import docker-compose.yml or use the
   Docker CLI via SSH
2. **PUID/PGID:** SSH into your NAS and run `id` for your admin user.
   Synology often uses UID `1026` or `1000` depending on account order.
3. **Stacks path:** Mount your compose files directory. Common locations:
   - `/volume1/docker` (if stacks are in the docker shared folder)
   - `/volume1/data/docker` (varies by setup)
4. **Docker socket:** Available at `/var/run/docker.sock` via SSH.
   Container Manager may not expose this in the GUI — use CLI or compose.
5. **ACLs:** If you get permission errors despite correct PUID/PGID,
   Synology's ACLs may be overriding UNIX permissions. Fix via:
   ```bash
   sudo synoacltool -enforce-inherit /volume1/docker
   ```

### macOS (Docker Desktop)
Same as Linux — Docker Desktop exposes the Unix socket at the same path.
PUID/PGID should match your macOS user (usually `501:20`):
```bash
-e PUID=501 -e PGID=20
```

### Windows (WSL2 — recommended)
Run from a WSL2 terminal. Your stacks path should be a Linux path:
```bash
-v /mnt/c/DockerContainers:/stacks:ro
```
PUID/PGID: Use `1000:1000` (default WSL2 user).

### Windows (Docker Desktop, PowerShell)
```powershell
-v //var/run/docker.sock:/var/run/docker.sock:ro
```
Note the double forward slash for the socket path.

### Portainer
MapArr works with Portainer — deploy via Stacks using the docker-compose.yml.
However, if you run into issues, try deploying via CLI instead. The Servarr
Wiki recommends CLI/Compose over Portainer for *arr-related containers due to
how Portainer abstracts container configuration.

## Verify
```bash
docker logs maparr
# Should show: MapArr v1.5.0 / Running as UID: <your PUID> GID: <your PGID>

curl http://localhost:9494/api/health
# {"status":"ok","version":"1.5.0"}
```

## Custom Image Recognition

MapArr ships with a database of 200+ Docker images it recognizes automatically. If you run a custom or self-built image that MapArr doesn't classify correctly, you can add a custom override file.

Create a `custom-images.json` file and mount it into the container:

```yaml
volumes:
  - ./custom-images.json:/data/custom-images.json:ro
```

Example `custom-images.json`:

```json
{
  "version": 1,
  "families": {},
  "images": {
    "my-custom-arr": {
      "name": "My Custom Arr App",
      "role": "arr",
      "family": "linuxserver",
      "patterns": ["myregistry/my-custom-arr"],
      "keywords": ["my-custom-arr"],
      "hardlink_capable": true,
      "docs_url": "https://github.com/me/my-custom-arr"
    }
  }
}
```

**Fields:**
- `role`: `"arr"`, `"download_client"`, `"media_server"`, `"request"`, or `"other"`
- `family`: `"linuxserver"`, `"hotio"`, `"jlesage"`, `"binhex"`, or `null` for independent images
- `patterns`: substrings to match against the Docker image string
- `keywords`: substrings to match against the service name
- `hardlink_capable`: whether this service participates in hardlink analysis

Your custom file persists across MapArr updates — it's your file, not ours.

## Troubleshooting

**Permission denied on /stacks:**
Your PUID/PGID doesn't match the owner of the stacks directory. Run `ls -la`
on the host to check ownership, then set PUID/PGID to match.

**"docker compose config unavailable" in logs:**
MapArr can't reach the Docker daemon. Either mount the socket or set
DOCKER_HOST for your socket proxy. This is non-fatal — MapArr falls back to
manual YAML parsing, which works for most *arr stacks.

**Port conflict:**
Change the port: `-e MAPARR_PORT=9595 -p 9595:9595`

**403 "Cannot browse system directories" when setting stacks path:**
MapArr blocks system directories (`/etc`, `/proc`, `/sys`, `/root`, `/home`, etc.)
from being scanned as a security precaution. If your compose files live under
`/home`, move them to a dedicated directory like `/opt/docker`, `/srv/docker`,
or `/data/docker` and mount that instead. This prevents accidental exposure of
user home directories (SSH keys, shell history, credentials) through the
directory browser.

**Apply Fix not working:**
Mount your stacks directory without `:ro` (read-only) — MapArr needs write
access to save corrected compose files.

## Without Docker (development)
```bash
cd maparr
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 9494
```
Open http://localhost:9494. Set `MAPARR_STACKS_PATH` to your stacks directory.
