# MapArr v1.0 — Quick Start

## Prerequisites

- **Docker** v20.10+ → [Install Docker](https://docs.docker.com/get-docker/)
- **Docker Compose** v2.0+ (included with Docker Desktop)

Verify:
```bash
docker --version
docker compose version
```

## Build
```bash
git clone https://github.com/coaxk/maparr.git
cd maparr
docker build -t maparr:v1.0 .
```

Build time: ~60–90 seconds | Image size: ~200–250MB

## Configure (optional)
```bash
cp .env.example .env
# Edit .env to change port, log level, etc.
```

## Run
```bash
docker-compose up -d
```

## Verify
```bash
docker-compose ps                        # Check status
curl http://localhost:9900/health         # Health check
docker-compose logs -f                   # Stream logs
```

## Access

Open **http://localhost:9900** in your browser.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MAPARR_PORT` | `9900` | Host port for MapArr web UI |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Path to Docker socket on host |
| `LOG_LEVEL` | `info` | Logging: `debug`, `info`, `warning`, `error` |
| `LOG_RETENTION_DAYS` | `7` | Days to retain application logs |
| `LOG_MAX_SIZE` | `100M` | Max Docker log file size |
| `LOG_MAX_FILES` | `5` | Max number of rotated Docker log files |

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health status + Docker connection info |
| `/api/docker/status` | GET | Docker connection details |
| `/api/containers` | GET | List all containers and their volume mounts |
| `/api/analyze` | POST | Analyze paths and detect conflicts |
| `/api/recommendations` | GET | Get setup recommendations |

## Stop
```bash
docker-compose down    # Stop and remove containers
docker-compose down -v # Also delete data volumes
```
