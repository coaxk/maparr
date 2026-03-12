# MapArr v1.0 — Troubleshooting

## Docker Not Connected

**Symptom:** `/api/docker/status` returns `"connected": false`

MapArr still runs without Docker access — container scanning is simply disabled.

**Fixes:**

1. Verify Docker is running:
```bash
docker info
```

2. Check the socket exists:
```bash
ls -la /var/run/docker.sock
```

3. Non-standard socket path? Set in `.env`:
```
DOCKER_SOCKET=/your/path/docker.sock
```

4. Permission denied?
```bash
sudo chmod 666 /var/run/docker.sock
```

---

## Port Already in Use

**Symptom:** `Bind for 0.0.0.0:9900 failed: port is already allocated`

1. Find the conflict:
```bash
lsof -i :9900          # macOS/Linux
ss -tlnp | grep 9900   # Linux
```

2. Change port in `.env`:
```
MAPARR_PORT=9901
```

3. Restart:
```bash
docker-compose down && docker-compose up -d
```

---

## Build Fails

**Frontend (Stage 2) fails:**
The Dockerfile handles this gracefully — it creates an empty `dist/` and continues.
The backend API will work regardless. Check that `frontend/index.html` is not empty.

**Python dependencies fail:**
```bash
docker builder prune -f
docker build --no-cache -t maparr:v1.0 .
```

**Out of disk space:**
```bash
docker system df              # Check usage
docker system prune -f        # Clean unused images/containers
docker builder prune -f       # Clean build cache
```

**Docker daemon not running:**
```bash
sudo systemctl start docker   # Linux
# macOS/Windows: Open Docker Desktop
```

---

## Container Keeps Restarting

1. Check logs:
```bash
docker-compose logs --tail=50 maparr
```

2. Debug interactively:
```bash
docker run -it --rm -p 9900:9900 maparr:v1.0 /bin/bash
uvicorn backend.main:app --host 0.0.0.0 --port 9900
```

---

## Health Check Shows Unhealthy
```bash
# Test from inside container
docker exec maparr curl -f http://localhost:9900/health

# Test from host
curl http://localhost:9900/health
```

If the app responds but Docker shows unhealthy, increase `start_period` in `docker-compose.yml`.

---

## No Logs Appearing

- `PYTHONUNBUFFERED=1` is set by default in the Dockerfile
- Set `LOG_LEVEL=debug` in `.env` for verbose output
- View logs: `docker-compose logs -f --tail=100`

---

## 403 Error When Setting Stacks Path

**Symptom:** Setting the stacks path to a directory under `/home` returns `403 — Cannot browse system directories`

MapArr blocks certain system directories from being scanned. The full blocklist:

```
/etc, /proc, /sys, /dev, /boot, /sbin, /root, /home
C:\Windows, C:\Program Files
```

**Why `/home` is blocked:** The directory browser would expose user home directories, which commonly contain SSH keys (`~/.ssh`), shell history, credentials files, and other sensitive data. Since MapArr has no authentication, any browser on the network could see these.

**Solutions:**

1. **Move stacks to a dedicated directory** (recommended):
```bash
# Common locations
/opt/docker/stacks
/srv/docker
/data/docker
```

2. **Mount only the specific subdirectory** into the container:
```yaml
# Instead of mounting all of /home/user:
volumes:
  - /home/user/docker-stacks:/stacks:ro
```
This works because MapArr validates the *browseable* path, not the host mount source. The container sees `/stacks`, which is not in the blocklist.

---

## Windows / WSL2

- **WSL2:** Mount `/var/run/docker.sock` as normal
- **Docker Desktop:** Socket forwarding is automatic
- MapArr handles both `/` and `\\` path formats automatically
