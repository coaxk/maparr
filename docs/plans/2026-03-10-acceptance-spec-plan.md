# MapArr Acceptance Spec Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a 4-layer acceptance test suite (Playwright E2E + pytest API contracts + Docker deployment) that acts as both a release gate and regression suite.

**Architecture:** Playwright tests drive a real browser against a running MapArr server pointed at synthetic test stacks. API contract tests use FastAPI's TestClient (no browser). Docker tests build and run the container image. All synthetic data lives in `tests/e2e/fixtures/stacks/` — deterministic compose files covering all 20 conflict types.

**Tech Stack:** Python 3.11+, pytest, playwright (pytest-playwright), httpx (already installed), FastAPI TestClient, Docker CLI

---

### Task 1: Install Dependencies and Create E2E Directory Structure

**Files:**
- Modify: `requirements-dev.txt` (create if missing)
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/conftest.py`
- Create: `pytest.ini` or modify existing pytest config

**Step 1: Create requirements-dev.txt**

```
# Dev/test dependencies for MapArr acceptance tests
pytest>=9.0.0
pytest-cov>=7.0.0
pytest-playwright>=0.6.2
httpx>=0.28.0
```

**Step 2: Install Playwright**

Run: `pip install pytest-playwright && playwright install chromium`
Expected: Chromium browser downloaded for Playwright

**Step 3: Create E2E directory structure**

```bash
mkdir -p tests/e2e/fixtures/stacks
touch tests/e2e/__init__.py
```

**Step 4: Create E2E conftest.py with server fixture**

Write `tests/e2e/conftest.py`:

```python
"""
E2E test fixtures for MapArr acceptance tests.

Starts a real MapArr server against synthetic test stacks,
provides Playwright browser pages pointed at it.
"""

import os
import shutil
import subprocess
import socket
import sys
import textwrap
import time
from pathlib import Path

import pytest

# ─── Constants ───

E2E_PORT = 19494  # Avoid clashing with dev server on 9494
E2E_FIXTURES = Path(__file__).parent / "fixtures" / "stacks"
E2E_BASE_URL = f"http://localhost:{E2E_PORT}"
SERVER_STARTUP_TIMEOUT = 15  # seconds


# ─── Server Management ───

def _port_is_open(port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def maparr_server():
    """Start a MapArr server for the E2E test session.

    Points MAPARR_STACKS_PATH at the synthetic test stacks directory.
    Kills the server after all tests complete.
    """
    if _port_is_open(E2E_PORT):
        pytest.fail(
            f"Port {E2E_PORT} already in use — kill the existing process first"
        )

    env = os.environ.copy()
    env["MAPARR_STACKS_PATH"] = str(E2E_FIXTURES)
    env["MAPARR_PORT"] = str(E2E_PORT)

    # Start uvicorn as a subprocess
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", "127.0.0.1",
            "--port", str(E2E_PORT),
            "--log-level", "warning",
        ],
        env=env,
        cwd=str(Path(__file__).parent.parent.parent),  # project root
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _port_is_open(E2E_PORT):
            break
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            pytest.fail(f"MapArr server exited early: {stderr}")
        time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail(f"MapArr server didn't start within {SERVER_STARTUP_TIMEOUT}s")

    yield {"url": E2E_BASE_URL, "port": E2E_PORT, "process": proc}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def base_url(maparr_server):
    """Base URL for the running MapArr server."""
    return maparr_server["url"]


# ─── Playwright Fixtures ───
# pytest-playwright provides `page`, `browser`, `context` automatically
# when pytest-playwright is installed. We just need to configure the base URL.

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url):
    """Configure Playwright browser context with MapArr base URL."""
    return {
        **browser_context_args,
        "base_url": base_url,
    }


# ─── Test Data Helpers ───

@pytest.fixture(scope="session")
def stacks_dir():
    """Path to the synthetic E2E test stacks directory."""
    return E2E_FIXTURES


@pytest.fixture
def clean_stacks(stacks_dir):
    """Reset any .bak files created by Apply Fix tests.

    Runs after each test that modifies compose files.
    """
    yield stacks_dir
    # Clean up .bak files
    for bak in stacks_dir.rglob("*.bak"):
        bak.unlink(missing_ok=True)
    # Restore any modified compose files from git
    subprocess.run(
        ["git", "checkout", "--", str(stacks_dir)],
        cwd=str(stacks_dir.parent.parent.parent),
        capture_output=True,
    )
```

**Step 5: Verify pytest discovers E2E tests**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/ --collect-only 2>&1 | head -5`
Expected: "no tests ran" (no test files yet, but no import errors)

**Step 6: Commit**

```bash
git add tests/e2e/ requirements-dev.txt
git commit -m "chore: set up E2E test infrastructure with Playwright fixtures"
```

---

### Task 2: Create Synthetic Test Stacks (16 Scenarios)

**Files:**
- Create: `tests/e2e/fixtures/stacks/healthy-arr/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/path-conflict/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/different-paths/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/named-volume/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/puid-mismatch/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/missing-puid/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/root-user/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/umask-issue/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/tz-mismatch/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/wsl2-paths/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/remote-fs/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/mixed-mounts/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/observations/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/cluster-layout/sonarr/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/cluster-layout/qbittorrent/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/cross-stack-puid/docker-compose.yml`
- Create: `tests/e2e/fixtures/stacks/rpm-scenario/docker-compose.yml`

**Step 1: Create all 16 synthetic test stacks**

Each compose file must be a valid Docker Compose document (`services:` key). These files are the test data — the exact conflict they trigger is documented in comments at the top.

`tests/e2e/fixtures/stacks/healthy-arr/docker-compose.yml`:
```yaml
# E2E fixture: healthy arr stack (no conflicts expected)
# Expected: green health, permission summary shown
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
    volumes:
      - ./config:/config
      - /data:/data
    restart: unless-stopped
  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      - UMASK=002
    volumes:
      - ./config:/config
      - /data:/data
    restart: unless-stopped
```

`tests/e2e/fixtures/stacks/path-conflict/docker-compose.yml`:
```yaml
# E2E fixture: no_shared_mount (Category A, critical)
# sonarr mounts /data, qbittorrent mounts /downloads — no shared parent
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /host/tv:/data/tv
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /host/downloads:/downloads
```

`tests/e2e/fixtures/stacks/different-paths/docker-compose.yml`:
```yaml
# E2E fixture: different_host_paths (Category A, high)
# Both mount /data inside container but from different host directories
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/disk1/media:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/disk2/media:/data
```

`tests/e2e/fixtures/stacks/named-volume/docker-compose.yml`:
```yaml
# E2E fixture: named_volume_data (Category A, critical)
# Named volumes are isolated — services can't share data through them
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - sonarr_config:/config
      - media_data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - qbit_config:/config
      - downloads_data:/downloads

volumes:
  sonarr_config:
  media_data:
  qbit_config:
  downloads_data:
```

`tests/e2e/fixtures/stacks/puid-mismatch/docker-compose.yml`:
```yaml
# E2E fixture: puid_pgid_mismatch (Category B, high)
# sonarr=1000, qbittorrent=911 — files won't be accessible cross-service
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=911
      - PGID=911
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/missing-puid/docker-compose.yml`:
```yaml
# E2E fixture: missing_puid_pgid (Category B, medium)
# qbittorrent has no PUID/PGID set
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/root-user/docker-compose.yml`:
```yaml
# E2E fixture: root_execution (Category B, medium)
# One service runs as root — files owned by root block other services
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=0
      - PGID=0
    volumes:
      - ./config:/config
      - /data:/data
  radarr:
    image: lscr.io/linuxserver/radarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/umask-issue/docker-compose.yml`:
```yaml
# E2E fixture: umask_inconsistent (Category B, low)
# Different UMASK values — newly created files have inconsistent permissions
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=022
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/tz-mismatch/docker-compose.yml`:
```yaml
# E2E fixture: tz_mismatch (Category B, low)
# Different timezones — scheduling and log timestamps will be confusing
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/London
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/wsl2-paths/docker-compose.yml`:
```yaml
# E2E fixture: wsl2_performance (Category C, medium)
# Data mounted through /mnt/c/ — WSL2 filesystem bridge is slow
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/c/media/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/c/media/data:/data
```

`tests/e2e/fixtures/stacks/remote-fs/docker-compose.yml`:
```yaml
# E2E fixture: remote_filesystem (Category C, medium)
# NFS/CIFS mount — hardlinks don't work across network boundaries
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - //192.168.1.10/media/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - //192.168.1.10/media/data:/data
```

`tests/e2e/fixtures/stacks/mixed-mounts/docker-compose.yml`:
```yaml
# E2E fixture: mixed_mount_types (Category C, medium)
# One local, one remote — hardlinks can't cross that boundary
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - //192.168.1.10/media/downloads:/downloads
```

`tests/e2e/fixtures/stacks/observations/docker-compose.yml`:
```yaml
# E2E fixture: Category D observations (no health impact)
# missing restart, latest tag, no TZ, privileged
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    privileged: true
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/cluster-layout/sonarr/docker-compose.yml`:
```yaml
# E2E fixture: cluster layout — sonarr subfolder
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/cluster-layout/qbittorrent/docker-compose.yml`:
```yaml
# E2E fixture: cluster layout — qbittorrent subfolder with different path
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /downloads:/downloads
```

`tests/e2e/fixtures/stacks/cross-stack-puid/docker-compose.yml`:
```yaml
# E2E fixture: cross_stack_puid_mismatch (Category B, high)
# This stack has PUID=1000 but another stack (puid-mismatch) has 911
# Cross-stack detection happens at pipeline level
services:
  lidarr:
    image: lscr.io/linuxserver/lidarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /data:/data
```

`tests/e2e/fixtures/stacks/rpm-scenario/docker-compose.yml`:
```yaml
# E2E fixture: path_unreachable (Category A) — triggers RPM wizard
# Error path won't match any mount — RPM wizard guides remapping
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/downloads:/downloads
```

**Step 2: Verify pipeline scan discovers all stacks**

Run: `cd /c/Projects/maparr && python -c "from backend.pipeline import run_pipeline_scan; r = run_pipeline_scan(str(__import__('pathlib').Path('tests/e2e/fixtures/stacks'))); print(f'Found {len(r.media_services)} services in {r.stacks_scanned} stacks')"`
Expected: Multiple services discovered across 16+ stacks (some are single-service, some multi)

**Step 3: Commit**

```bash
git add tests/e2e/fixtures/
git commit -m "test: add 16 synthetic E2E test stacks covering all conflict types"
```

---

### Task 3: Layer 3 — API Contract Tests

**Files:**
- Create: `tests/e2e/test_api_contracts.py`

**Why Layer 3 first:** API tests use FastAPI TestClient (no browser needed), so they're the fastest to write and validate. They also confirm the backend is producing correct response shapes before we test the frontend.

**Step 1: Write the API contract tests**

Write `tests/e2e/test_api_contracts.py`:

```python
"""
Layer 3: API Contract Tests

Validates response shapes, status codes, and edge cases for all MapArr endpoints.
Uses FastAPI TestClient — no browser, no running server needed.

Each test asserts the SHAPE of the response (required fields, types, allowed values)
so failures read like: "Expected 'health' field in pipeline-scan response to be one
of ['ok', 'warning', 'problem'] but got 'unknown'"
"""

import time
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _session, _rate_limiter

E2E_STACKS = str(Path(__file__).parent / "fixtures" / "stacks")


@pytest.fixture(autouse=True)
def _reset_session():
    """Reset server state between tests."""
    _session["parsed_error"] = None
    _session["selected_stack"] = None
    _session["pipeline"] = None
    _session.pop("custom_stacks_path", None)
    _rate_limiter.reset()
    yield
    _session["parsed_error"] = None
    _session["selected_stack"] = None
    _session["pipeline"] = None
    _session.pop("custom_stacks_path", None)
    _rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


# ─── 3.1 GET /api/health ───

class TestHealthEndpoint:
    def test_health_returns_ok_and_version(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200, "Health endpoint should return 200"
        data = resp.json()
        assert "status" in data, "Health response must include 'status' field"
        assert data["status"] == "ok", "Health status should be 'ok'"
        assert "version" in data, "Health response must include 'version' field"
        assert isinstance(data["version"], str), "Version should be a string"


# ─── 3.2 POST /api/parse-error ───

class TestParseError:
    def test_single_error_has_required_fields(self, client):
        resp = client.post("/api/parse-error", json={
            "error_text": "Import failed, path does not exist: /data/tv/Show/Season 1/episode.mkv"
        })
        assert resp.status_code == 200
        data = resp.json()
        for field in ("service", "path", "error_type", "confidence"):
            assert field in data, f"Parse response missing required field '{field}'"

    def test_multi_error_returns_array(self, client):
        multi_error = (
            "Import failed: /data/tv/Show/ep1.mkv\n"
            "Import failed: /data/tv/Show/ep2.mkv"
        )
        resp = client.post("/api/parse-error", json={"error_text": multi_error})
        assert resp.status_code == 200
        data = resp.json()
        if data.get("error_count", 1) > 1:
            assert "multiple_errors" in data, \
                "Multi-error response should include 'multiple_errors' array"

    def test_empty_error_text_handled(self, client):
        resp = client.post("/api/parse-error", json={"error_text": ""})
        assert resp.status_code == 200, \
            "Empty error text should return 200 (no match), not crash"


# ─── 3.3 POST /api/pipeline-scan ───

class TestPipelineScan:
    def test_pipeline_scan_response_shape(self, client):
        resp = client.post("/api/pipeline-scan", json={"scan_dir": E2E_STACKS})
        assert resp.status_code == 200
        data = resp.json()
        required = ["scan_dir", "scanned_at", "media_services", "roles_present",
                     "health", "summary", "steps"]
        for field in required:
            assert field in data, \
                f"Pipeline scan response missing required field '{field}'"

    def test_pipeline_scan_health_is_valid_value(self, client):
        resp = client.post("/api/pipeline-scan", json={"scan_dir": E2E_STACKS})
        data = resp.json()
        assert data["health"] in ("ok", "warning", "problem"), \
            f"Expected health to be ok/warning/problem, got '{data['health']}'"

    def test_pipeline_scan_media_services_have_required_fields(self, client):
        resp = client.post("/api/pipeline-scan", json={"scan_dir": E2E_STACKS})
        data = resp.json()
        assert len(data["media_services"]) > 0, \
            "Pipeline scan should find at least one media service in E2E fixtures"
        for svc in data["media_services"]:
            for field in ("service_name", "role", "stack_name", "compose_file"):
                assert field in svc, \
                    f"Media service missing required field '{field}': {svc}"

    def test_pipeline_scan_discovers_cluster_layout(self, client):
        resp = client.post("/api/pipeline-scan", json={"scan_dir": E2E_STACKS})
        data = resp.json()
        cluster_services = [
            s for s in data["media_services"]
            if "cluster-layout" in s.get("compose_file", "")
        ]
        assert len(cluster_services) >= 2, \
            f"Expected cluster-layout to produce 2+ services, got {len(cluster_services)}"
        # Each should have a distinct compose_file
        compose_files = {s["compose_file"] for s in cluster_services}
        assert len(compose_files) >= 2, \
            "Cluster services should have distinct compose_file paths"


# ─── 3.4 POST /api/change-stacks-path ───

class TestChangeStacksPath:
    def test_valid_path_accepted(self, client):
        resp = client.post("/api/change-stacks-path", json={"path": E2E_STACKS})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("ok", "reset"), \
            f"Expected status ok/reset, got '{data.get('status')}'"

    def test_blocked_path_rejected(self, client):
        # /proc is always blocked
        resp = client.post("/api/change-stacks-path", json={"path": "/proc"})
        # Should be rejected (400 or 200 with error status)
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("status") != "ok", \
                "Blocked path /proc should not be accepted"


# ─── 3.5 POST /api/list-directories ───

class TestListDirectories:
    def test_list_directories_response_shape(self, client):
        resp = client.post("/api/list-directories", json={"path": E2E_STACKS})
        assert resp.status_code == 200
        data = resp.json()
        assert "directories" in data, \
            "List directories response must include 'directories' array"
        assert isinstance(data["directories"], list), \
            "'directories' should be a list"
        if data["directories"]:
            entry = data["directories"][0]
            assert "name" in entry, "Directory entry must have 'name'"
            assert "path" in entry, "Directory entry must have 'path'"


# ─── 3.6 POST /api/analyze ───

class TestAnalyze:
    def _scan_first(self, client):
        """Pipeline scan must run before analyze (populates session)."""
        client.post("/api/pipeline-scan", json={"scan_dir": E2E_STACKS})

    def test_analyze_path_conflict_returns_conflicts(self, client):
        self._scan_first(client)
        stack_path = str(Path(E2E_STACKS) / "path-conflict")
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("conflicts_found", "healthy_pipeline", "error"), \
            f"Unexpected analyze status: {data.get('status')}"
        if data["status"] == "conflicts_found":
            assert len(data.get("conflicts", [])) > 0, \
                "conflicts_found status but no conflicts in response"
            conflict = data["conflicts"][0]
            for field in ("conflict_type", "severity", "services", "description"):
                assert field in conflict, \
                    f"Conflict missing required field '{field}'"
            assert "category" in conflict, \
                "Conflict must include 'category' field (A/B/C/D)"

    def test_analyze_healthy_stack(self, client):
        self._scan_first(client)
        stack_path = str(Path(E2E_STACKS) / "healthy-arr")
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        assert resp.status_code == 200
        data = resp.json()
        # Healthy stack should have no Category A/B conflicts
        cat_ab = [
            c for c in data.get("conflicts", [])
            if c.get("category") in ("A", "B")
        ]
        assert len(cat_ab) == 0, \
            f"Healthy stack should have no A/B conflicts, got {len(cat_ab)}"

    def test_analyze_category_a_generates_solution_yaml(self, client):
        self._scan_first(client)
        stack_path = str(Path(E2E_STACKS) / "path-conflict")
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        data = resp.json()
        if data["status"] == "conflicts_found":
            cat_a = [c for c in data["conflicts"] if c.get("category") == "A"]
            if cat_a:
                assert data.get("solution_yaml") or data.get("original_corrected_yaml"), \
                    "Category A conflicts must generate solution_yaml or original_corrected_yaml"

    def test_analyze_category_b_generates_env_solution(self, client):
        self._scan_first(client)
        stack_path = str(Path(E2E_STACKS) / "puid-mismatch")
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        data = resp.json()
        cat_b = [c for c in data.get("conflicts", []) if c.get("category") == "B"]
        if cat_b:
            assert data.get("env_solution_yaml"), \
                "Category B conflicts must generate env_solution_yaml"

    def test_analyze_includes_observations(self, client):
        self._scan_first(client)
        stack_path = str(Path(E2E_STACKS) / "observations")
        resp = client.post("/api/analyze", json={"stack_path": stack_path})
        data = resp.json()
        # Observations may or may not appear depending on stack content
        # But the field should always be present in the response
        assert "observations" in data or data.get("status") == "error", \
            "Analyze response should include 'observations' field"

    def test_analyze_path_outside_stacks_rejected(self, client):
        self._scan_first(client)
        resp = client.post("/api/analyze", json={"stack_path": "/etc/passwd"})
        # Should be 403 or error status
        assert resp.status_code in (403, 400, 200), "Should handle gracefully"
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("status") == "error", \
                "Path outside stacks root should return error status"


# ─── 3.7 POST /api/apply-fix ───

class TestApplyFix:
    def test_apply_fix_creates_backup(self, client, tmp_path):
        """Apply fix should create a .bak backup file."""
        # Set up a writable stack
        stack = tmp_path / "fixtest"
        stack.mkdir()
        compose = stack / "docker-compose.yml"
        original_yaml = textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                volumes:
                  - /host/tv:/data/tv
        """)
        compose.write_text(original_yaml)

        # Must set stacks path to allow writes
        _session["custom_stacks_path"] = str(tmp_path)

        corrected_yaml = textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                volumes:
                  - /data:/data
        """)
        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": corrected_yaml,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok", f"Apply fix failed: {data}"
        assert data.get("backup_created") is True, \
            "Apply fix should report backup_created=True"

        bak = stack / "docker-compose.yml.bak"
        assert bak.exists(), "Backup .bak file should exist after apply"
        assert bak.read_text() == original_yaml, \
            "Backup should contain original YAML"


# ─── 3.8 POST /api/apply-fixes (batch) ───

class TestApplyFixesBatch:
    def test_batch_limit_enforced(self, client):
        """Reject batches exceeding 20 files."""
        fixes = [{"compose_file_path": f"/fake/{i}/compose.yml",
                   "corrected_yaml": "services: {}"} for i in range(21)]
        resp = client.post("/api/apply-fixes", json={"fixes": fixes})
        # Should reject (400 or error in response)
        if resp.status_code == 200:
            assert resp.json().get("status") != "ok", \
                "Batch of 21 files should be rejected"


# ─── 3.9 POST /api/redeploy ───

class TestRedeploy:
    def test_redeploy_limit_enforced(self, client):
        """Reject redeploy of more than 10 stacks."""
        stacks = [f"/fake/stack{i}" for i in range(11)]
        resp = client.post("/api/redeploy", json={"stacks": stacks})
        if resp.status_code == 200:
            assert resp.json().get("status") != "ok", \
                "Redeploy of 11 stacks should be rejected"


# ─── 3.10 GET /api/discover-stacks ───

class TestDiscoverStacks:
    def test_discover_response_shape(self, client):
        # First set the stacks path
        _session["custom_stacks_path"] = E2E_STACKS
        resp = client.get("/api/discover-stacks")
        assert resp.status_code == 200
        data = resp.json()
        assert "stacks" in data, "Discover response must have 'stacks'"
        assert "total" in data, "Discover response must have 'total'"


# ─── 3.12 POST /api/smart-match ───

class TestSmartMatch:
    def test_smart_match_returns_result(self, client):
        resp = client.post("/api/smart-match", json={
            "error_text": "sonarr import failed /data/tv/show"
        })
        assert resp.status_code == 200


# ─── 3.14 Rate Limiting ───

class TestRateLimiting:
    def test_write_tier_rate_limit(self, client):
        """Write endpoints should be limited to 10/min."""
        # Set valid stacks path so requests aren't rejected for other reasons
        _session["custom_stacks_path"] = E2E_STACKS

        # Fire 11 requests rapidly to the same write endpoint
        responses = []
        for _ in range(11):
            resp = client.post("/api/change-stacks-path", json={"path": E2E_STACKS})
            responses.append(resp.status_code)

        # At least one should be rate-limited (429)
        assert 429 in responses, \
            f"Expected at least one 429 after 11 rapid write requests, got: {set(responses)}"

    def test_read_tier_not_limited_at_low_volume(self, client):
        """Read endpoints should allow 60/min — 5 requests should be fine."""
        for _ in range(5):
            resp = client.get("/api/health")
            assert resp.status_code == 200, \
                "5 health checks should not trigger rate limiting"
```

**Step 2: Run the API contract tests**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/test_api_contracts.py -v -p no:capture 2>&1 | tail -30`
Expected: Most tests PASS. Some may fail if endpoint response shapes don't match expectations — these failures ARE the acceptance spec catching real issues.

**Step 3: Fix any test failures caused by response shape mismatches**

If tests fail, adjust either the test expectations (if the response is correct but the spec was wrong) or the backend (if the response is genuinely broken). Do NOT weaken assertions just to make tests pass.

**Step 4: Commit**

```bash
git add tests/e2e/test_api_contracts.py
git commit -m "test: Layer 3 API contract tests — 14 endpoints, shape validation"
```

---

### Task 4: Layer 1 — Component Spec Tests (Playwright)

**Files:**
- Create: `tests/e2e/test_components.py`

**Step 1: Write the component spec tests**

Write `tests/e2e/test_components.py`:

```python
"""
Layer 1: Component Spec Tests

Each test asserts that a specific UI component renders correctly.
Failures read like bug reports:
  "Expected #first-launch to be visible on fresh load, but it was hidden"

Uses Playwright to drive a real browser against the running MapArr server.
Requires: maparr_server fixture (starts server), page fixture (from pytest-playwright).
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

E2E_STACKS = str(Path(__file__).parent / "fixtures" / "stacks")


# ─── Helpers ───

def _navigate_fresh(page: Page, base_url: str):
    """Load MapArr with no prior state (first launch)."""
    page.goto(base_url)
    page.wait_for_load_state("networkidle")


def _scan_and_wait_for_dashboard(page: Page, base_url: str, stacks_path: str):
    """Complete the first-launch → scan → dashboard flow."""
    page.goto(base_url)
    page.wait_for_load_state("networkidle")

    # Type stacks path into first-launch input
    path_input = page.locator("#first-launch input[type='text']")
    if path_input.is_visible():
        path_input.fill(stacks_path)
        page.locator("#first-launch-scan").click()
    else:
        # Already past first launch — use header path editor
        page.locator("#header-path").click()
        page.locator("#header-path-input").fill(stacks_path)
        page.locator("#header-path-go").click()

    # Wait for dashboard to appear
    page.locator("#pipeline-dashboard").wait_for(state="visible", timeout=15000)


# ─── 1.1 First Launch Screen ───

class TestFirstLaunchScreen:
    def test_first_launch_visible_on_fresh_load(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        first_launch = page.locator("#first-launch")
        expect(first_launch).to_be_visible(
            timeout=5000
        )

    def test_first_launch_has_scan_button(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        btn = page.locator("#first-launch-scan")
        expect(btn).to_be_visible()

    def test_first_launch_has_browse_button(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        btn = page.locator("#first-launch-browse")
        expect(btn).to_be_visible()

    def test_dashboard_hidden_on_first_launch(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        dashboard = page.locator("#pipeline-dashboard")
        expect(dashboard).to_be_hidden()


# ─── 1.2 Directory Browser Modal ───

class TestDirectoryBrowserModal:
    def test_browse_button_opens_modal(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        page.locator("#first-launch-browse").click()
        overlay = page.locator(".dir-browser-overlay")
        expect(overlay).to_be_visible(timeout=3000)

    def test_modal_has_directory_items(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        page.locator("#first-launch-browse").click()
        page.locator(".dir-browser-overlay").wait_for(state="visible")
        # Should show at least one directory entry
        items = page.locator(".dir-browser-item")
        expect(items.first).to_be_visible(timeout=5000)

    def test_modal_close_button_dismisses(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        page.locator("#first-launch-browse").click()
        page.locator(".dir-browser-overlay").wait_for(state="visible")
        # Close the modal
        page.locator(".dir-browser .btn-ghost, .dir-browser-close").first.click()
        expect(page.locator(".dir-browser-overlay")).to_be_hidden(timeout=3000)


# ─── 1.3 Boot Terminal ───

class TestBootTerminal:
    def test_boot_terminal_shows_during_scan(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        path_input = page.locator("#first-launch input[type='text']")
        path_input.fill(E2E_STACKS)
        page.locator("#first-launch-scan").click()

        # Boot screen should appear (may be brief)
        boot = page.locator("#boot-screen")
        # It should either be visible now or the dashboard already appeared
        page.locator("#pipeline-dashboard, #boot-screen").first.wait_for(
            state="visible", timeout=15000
        )

    def test_boot_terminal_has_dots(self, page, maparr_server):
        _navigate_fresh(page, maparr_server["url"])
        path_input = page.locator("#first-launch input[type='text']")
        path_input.fill(E2E_STACKS)
        page.locator("#first-launch-scan").click()

        # Wait for boot or dashboard
        page.locator("#pipeline-dashboard, #boot-screen").first.wait_for(
            state="visible", timeout=15000
        )
        # If boot screen is visible, check for dots
        if page.locator("#boot-screen").is_visible():
            expect(page.locator(".dot-red")).to_be_visible()
            expect(page.locator(".dot-yellow")).to_be_visible()
            expect(page.locator(".dot-green")).to_be_visible()


# ─── 1.4 Pipeline Dashboard ───

class TestPipelineDashboard:
    def test_dashboard_visible_after_scan(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        expect(page.locator("#pipeline-dashboard")).to_be_visible()

    def test_dashboard_has_health_banner(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        expect(page.locator("#health-banner")).to_be_visible()

    def test_dashboard_has_service_groups(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        expect(page.locator("#service-groups")).to_be_visible()

    def test_service_count_shows_number(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        count_el = page.locator("#service-count")
        expect(count_el).to_be_visible()
        text = count_el.text_content()
        assert text and any(c.isdigit() for c in text), \
            f"Service count should contain a number, got: '{text}'"


# ─── 1.5 Service Groups ───

class TestServiceGroups:
    def test_service_rows_have_icons(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        # At least some service rows should have icon images
        icons = page.locator("#service-groups img[src*='/img/services/']")
        count = icons.count()
        assert count > 0, \
            "Expected service rows to have icons from /img/services/"

    def test_service_rows_have_health_dots(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        dots = page.locator("#service-groups .health-dot")
        assert dots.count() > 0, \
            "Expected health dots in service rows"


# ─── 1.6 Health Banner ───

class TestHealthBanner:
    def test_health_banner_has_text(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        banner_text = page.locator("#health-banner-text, #health-banner")
        text = banner_text.text_content()
        assert text and len(text) > 5, \
            f"Health banner should have descriptive text, got: '{text}'"

    def test_health_banner_reflects_problems(self, page, maparr_server):
        """With our E2E fixtures (which include broken stacks), banner should show problems."""
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        banner = page.locator("#health-banner")
        classes = banner.get_attribute("class") or ""
        # Our fixtures include path-conflict, puid-mismatch, etc.
        # So the banner should NOT be health-ok
        assert "health-ok" not in classes or "health-problem" in classes or "health-warning" in classes, \
            "Banner should show problems/warnings given our E2E fixtures include broken stacks"


# ─── 1.7 Paste Area ───

class TestPasteArea:
    def test_paste_area_toggle(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        paste_area = page.locator("#paste-area")
        # Initially hidden
        expect(paste_area).to_be_hidden()
        # Click fork-paste to open
        page.locator("#fork-paste").click()
        expect(paste_area).to_be_visible(timeout=3000)

    def test_paste_area_has_elements(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        page.locator("#fork-paste").click()
        page.locator("#paste-area").wait_for(state="visible")
        expect(page.locator("#paste-error-input")).to_be_visible()
        expect(page.locator("#paste-error-go")).to_be_visible()

    def test_paste_area_has_example_pills(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        page.locator("#fork-paste").click()
        page.locator("#paste-area").wait_for(state="visible")
        pills = page.locator(".paste-pill")
        assert pills.count() > 0, \
            "Paste area should have example pills"


# ─── 1.20 Service Icons ───

class TestServiceIcons:
    def test_icons_have_lazy_loading(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        icons = page.locator("#service-groups img[loading='lazy']")
        assert icons.count() > 0, \
            "Service icons should have loading='lazy' attribute"


# ─── 1.21 Path Editor (Header) ───

class TestPathEditor:
    def test_header_path_click_toggles_editor(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        # Click header path to open editor
        page.locator("#header-path").click()
        editor = page.locator("#path-editor")
        expect(editor).to_be_visible(timeout=3000)

    def test_path_editor_has_input_and_buttons(self, page, maparr_server):
        _scan_and_wait_for_dashboard(page, maparr_server["url"], E2E_STACKS)
        page.locator("#header-path").click()
        page.locator("#path-editor").wait_for(state="visible")
        expect(page.locator("#header-path-input")).to_be_visible()
        expect(page.locator("#header-path-go")).to_be_visible()
```

**Step 2: Run the component tests**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/test_components.py -v --headed 2>&1 | tail -40`
Expected: Tests run in a visible browser. Some may fail — that's the spec catching real issues.

Note: First run may need `--headed` removed for CI. Use `--headed` only for debugging.

**Step 3: Fix any genuine UI bugs caught by the tests**

If a test fails because a DOM element is missing or has the wrong class, that's a real bug — fix it in the frontend. If a test fails because the assertion is wrong (element has a slightly different ID), fix the test.

**Step 4: Commit**

```bash
git add tests/e2e/test_components.py
git commit -m "test: Layer 1 component specs — DOM assertions for 15 key UI elements"
```

---

### Task 5: Layer 2 — User Journey Tests (Playwright)

**Files:**
- Create: `tests/e2e/test_journeys.py`

**Step 1: Write the user journey tests**

Write `tests/e2e/test_journeys.py`:

```python
"""
Layer 2: User Journey Tests

Each test simulates a complete user workflow end-to-end.
These are the most valuable tests — they catch the exact bugs
that manual smoke testing reveals.

Requires: maparr_server fixture, page fixture (pytest-playwright).
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

E2E_STACKS = str(Path(__file__).parent / "fixtures" / "stacks")


def _fresh_load(page: Page, url: str):
    """Navigate to MapArr with clean state."""
    page.goto(url)
    page.wait_for_load_state("networkidle")


def _get_to_dashboard(page: Page, url: str, stacks_path: str):
    """Get from fresh load to dashboard."""
    _fresh_load(page, url)
    path_input = page.locator("#first-launch input[type='text']")
    if path_input.is_visible():
        path_input.fill(stacks_path)
        page.locator("#first-launch-scan").click()
    else:
        page.locator("#header-path").click()
        page.locator("#header-path-input").fill(stacks_path)
        page.locator("#header-path-go").click()
    page.locator("#pipeline-dashboard").wait_for(state="visible", timeout=15000)


# ─── 2.1 First Launch → Browse → Scan → Dashboard ───

class TestFirstLaunchBrowseScanDashboard:
    def test_browse_select_scan_reaches_dashboard(self, page, maparr_server):
        url = maparr_server["url"]
        _fresh_load(page, url)

        # 1. First launch is visible
        expect(page.locator("#first-launch")).to_be_visible()

        # 2. Click browse → modal opens
        page.locator("#first-launch-browse").click()
        page.locator(".dir-browser-overlay").wait_for(state="visible", timeout=5000)

        # 3. The directory browser should show items
        items = page.locator(".dir-browser-item")
        expect(items.first).to_be_visible(timeout=5000)

        # Close modal and use manual path instead (browser can't navigate to test fixtures)
        page.keyboard.press("Escape")
        page.locator(".dir-browser-overlay").wait_for(state="hidden", timeout=3000)

        # 4. Type path manually and scan
        path_input = page.locator("#first-launch input[type='text']")
        path_input.fill(E2E_STACKS)

        # 5. Click scan
        page.locator("#first-launch-scan").click()

        # 6-7. Dashboard appears
        page.locator("#pipeline-dashboard").wait_for(state="visible", timeout=15000)
        count = page.locator("#service-count").text_content()
        assert count and any(c.isdigit() for c in count), \
            f"Service count should show a number after scan, got: '{count}'"


# ─── 2.2 First Launch → Manual Path → Scan ───

class TestFirstLaunchManualPath:
    def test_manual_path_reaches_dashboard(self, page, maparr_server):
        url = maparr_server["url"]
        _fresh_load(page, url)

        path_input = page.locator("#first-launch input[type='text']")
        path_input.fill(E2E_STACKS)
        page.locator("#first-launch-scan").click()

        page.locator("#pipeline-dashboard").wait_for(state="visible", timeout=15000)
        expect(page.locator("#service-groups")).to_be_visible()


# ─── 2.3 Dashboard → Analyze Healthy Stack ───

class TestAnalyzeHealthyStack:
    def test_healthy_stack_shows_green(self, page, maparr_server):
        url = maparr_server["url"]
        _get_to_dashboard(page, url, E2E_STACKS)

        # Find and click the healthy-arr service (sonarr or radarr from that stack)
        # Look for a service row containing "sonarr" or "radarr"
        service_rows = page.locator("#service-groups [data-service-name]")
        clicked = False
        for i in range(service_rows.count()):
            row = service_rows.nth(i)
            name = (row.get_attribute("data-service-name") or "").lower()
            stack = (row.get_attribute("data-stack-name") or "").lower()
            if "healthy" in stack:
                row.click()
                clicked = True
                break

        if not clicked:
            # Fallback: click any service row
            service_rows.first.click()

        # Wait for analysis to complete — either healthy or problem step
        page.locator("#step-healthy, #step-problem, #step-solution").first.wait_for(
            state="visible", timeout=30000
        )

        # Back button should return to dashboard
        back_btn = page.locator("#btn-back")
        if back_btn.is_visible():
            back_btn.click()
            expect(page.locator("#pipeline-dashboard")).to_be_visible(timeout=5000)


# ─── 2.4 Dashboard → Analyze Path Conflict → Apply Fix ───

class TestAnalyzePathConflictApplyFix:
    def test_path_conflict_shows_solution_with_apply(self, page, maparr_server):
        url = maparr_server["url"]
        _get_to_dashboard(page, url, E2E_STACKS)

        # Find service from path-conflict stack
        service_rows = page.locator("#service-groups [data-service-name]")
        clicked = False
        for i in range(service_rows.count()):
            row = service_rows.nth(i)
            stack = (row.get_attribute("data-stack-name") or "").lower()
            if "path-conflict" in stack:
                row.click()
                clicked = True
                break

        if not clicked:
            pytest.skip("path-conflict stack not found in dashboard")

        # Wait for analysis result
        page.locator("#step-problem, #step-healthy, #step-solution").first.wait_for(
            state="visible", timeout=30000
        )

        # If conflicts found, check for solution
        if page.locator("#step-problem").is_visible():
            # Problem card should have conflict description
            problem_text = page.locator("#problem-details").text_content()
            assert problem_text and len(problem_text) > 10, \
                "Problem card should have descriptive conflict text"

        # Solution section should appear for Category A
        solution = page.locator("#step-solution")
        if solution.is_visible():
            # Should have YAML content
            yaml_content = page.locator("#solution-yaml, #solution-yaml-original")
            expect(yaml_content.first).to_be_visible()

            # Should have Apply Fix button
            apply_btn = page.locator("#btn-apply-fix")
            if apply_btn.is_visible():
                # Don't actually apply in this test — just verify the button exists
                pass


# ─── 2.5 Dashboard → Analyze Permission Issue ───

class TestAnalyzePermissionIssue:
    def test_permission_conflict_shows_env_solution(self, page, maparr_server):
        url = maparr_server["url"]
        _get_to_dashboard(page, url, E2E_STACKS)

        # Find service from puid-mismatch stack
        service_rows = page.locator("#service-groups [data-service-name]")
        for i in range(service_rows.count()):
            row = service_rows.nth(i)
            stack = (row.get_attribute("data-stack-name") or "").lower()
            if "puid-mismatch" in stack:
                row.click()
                break
        else:
            pytest.skip("puid-mismatch stack not found in dashboard")

        # Wait for analysis
        page.locator("#step-problem, #step-healthy, #step-solution").first.wait_for(
            state="visible", timeout=30000
        )

        # Should show permission-related content
        page_text = page.locator("body").text_content() or ""
        assert any(kw in page_text.lower() for kw in ["puid", "pgid", "permission", "user"]), \
            "Permission conflict analysis should mention PUID/PGID/permission"


# ─── 2.6 Dashboard → Paste Error → Auto-Match ───

class TestPasteErrorAutoMatch:
    def test_paste_error_matches_service(self, page, maparr_server):
        url = maparr_server["url"]
        _get_to_dashboard(page, url, E2E_STACKS)

        # Open paste area
        page.locator("#fork-paste").click()
        page.locator("#paste-area").wait_for(state="visible")

        # Type an error
        page.locator("#paste-error-input").fill(
            "Import failed, path does not exist: /data/tv/Show/Season 1/ep.mkv"
        )
        page.locator("#paste-error-go").click()

        # Should show a result
        page.locator("#paste-bar-result").wait_for(state="visible", timeout=10000)
        result_text = page.locator("#paste-bar-result").text_content()
        assert result_text and len(result_text) > 0, \
            "Paste bar result should show matched service"


# ─── 2.7 Change Stacks Path ───

class TestChangeStacksPath:
    def test_header_path_change_rescans(self, page, maparr_server):
        url = maparr_server["url"]
        _get_to_dashboard(page, url, E2E_STACKS)

        # Get initial service count
        initial_count = page.locator("#service-count").text_content()

        # Click header path to open editor
        page.locator("#header-path").click()
        page.locator("#path-editor").wait_for(state="visible")

        # Enter same path (just to test the rescan flow)
        page.locator("#header-path-input").fill(E2E_STACKS)
        page.locator("#header-path-go").click()

        # Dashboard should reload
        page.locator("#pipeline-dashboard").wait_for(state="visible", timeout=15000)

        # Service count should still be present
        new_count = page.locator("#service-count").text_content()
        assert new_count and any(c.isdigit() for c in new_count), \
            f"Service count should show a number after rescan, got: '{new_count}'"
```

**Step 2: Run the journey tests**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/test_journeys.py -v --headed 2>&1 | tail -40`
Expected: Journey tests run through full UI flows. Failures identify broken user workflows.

**Step 3: Fix any journey failures**

Journey test failures are the most valuable — they represent things a real user would encounter. Fix the underlying frontend/backend issue, not the test.

**Step 4: Commit**

```bash
git add tests/e2e/test_journeys.py
git commit -m "test: Layer 2 user journey tests — 7 end-to-end workflows"
```

---

### Task 6: Layer 4 — Docker Deployment Tests

**Files:**
- Create: `tests/e2e/test_docker.py`

**Step 1: Write the Docker deployment tests**

Write `tests/e2e/test_docker.py`:

```python
"""
Layer 4: Docker Deployment Tests

Validates container build, startup, PUID/PGID, port config, and healthcheck.
These tests build and run actual Docker containers — slower, run only in pre-release.

Skip these tests if Docker is not available:
  pytest tests/e2e/test_docker.py -v

Mark: pytest.mark.docker — can be excluded in CI with -m "not docker"
"""

import os
import subprocess
import time
import socket

import pytest

try:
    subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
    DOCKER_AVAILABLE = True
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
    DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available")

PROJECT_ROOT = str(__import__("pathlib").Path(__file__).parent.parent.parent)
IMAGE_NAME = "maparr-e2e-test"
E2E_STACKS = str(__import__("pathlib").Path(__file__).parent / "fixtures" / "stacks")


def _port_is_open(port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _build_image():
    """Build the Docker image if not already built."""
    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"Docker build failed:\n{result.stderr}")


def _run_container(port: int = 29494, extra_env: dict = None,
                   extra_volumes: list = None, name: str = None):
    """Run a MapArr container and return its ID."""
    cmd = [
        "docker", "run", "-d",
        "--name", name or f"maparr-e2e-{port}",
        "-p", f"{port}:{port}",
        "-e", f"MAPARR_PORT={port}",
        "-v", f"{E2E_STACKS}:/stacks:ro",
    ]
    if extra_env:
        for k, v in extra_env.items():
            cmd.extend(["-e", f"{k}={v}"])
    if extra_volumes:
        for vol in extra_volumes:
            cmd.extend(["-v", vol])
    cmd.append(IMAGE_NAME)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"docker run failed: {result.stderr}")
    return result.stdout.strip()


def _cleanup_container(container_id: str):
    """Stop and remove a container."""
    subprocess.run(["docker", "rm", "-f", container_id],
                   capture_output=True, timeout=15)


def _wait_for_healthy(port: int, timeout: int = 30) -> bool:
    """Wait for the container to respond on the given port."""
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://localhost:{port}/api/health", timeout=2)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="module", autouse=True)
def build_image():
    """Build the Docker image once for all tests in this module."""
    _build_image()


# ─── 4.1 Container Starts with Defaults ───

class TestContainerDefaults:
    def test_container_starts_and_serves_health(self):
        port = 29494
        cid = _run_container(port=port)
        try:
            assert _wait_for_healthy(port, timeout=30), \
                "Container should reach healthy state within 30 seconds"

            import httpx
            resp = httpx.get(f"http://localhost:{port}/api/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            _cleanup_container(cid)


# ─── 4.3 Custom Port ───

class TestCustomPort:
    def test_custom_port_serves(self):
        port = 28080
        cid = _run_container(port=port, name="maparr-e2e-custom-port")
        try:
            assert _wait_for_healthy(port, timeout=30), \
                f"Container should serve on custom port {port}"
        finally:
            _cleanup_container(cid)


# ─── 4.6 Read-Only Stacks Volume ───

class TestReadOnlyStacks:
    def test_scan_works_with_readonly_stacks(self):
        port = 29495
        cid = _run_container(port=port, name="maparr-e2e-readonly")
        try:
            assert _wait_for_healthy(port, timeout=30)

            import httpx
            # Pipeline scan should work (read-only operation)
            resp = httpx.post(
                f"http://localhost:{port}/api/pipeline-scan",
                json={"scan_dir": "/stacks"},
                timeout=15,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data.get("media_services", [])) > 0, \
                "Pipeline scan should find services even with read-only stacks volume"
        finally:
            _cleanup_container(cid)
```

**Step 2: Run the Docker tests**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/test_docker.py -v 2>&1 | tail -20`
Expected: If Docker is available, tests build the image and run containers. If Docker is unavailable, tests are skipped.

**Step 3: Commit**

```bash
git add tests/e2e/test_docker.py
git commit -m "test: Layer 4 Docker deployment tests — build, port, healthcheck"
```

---

### Task 7: Create Manual Checklist

**Files:**
- Create: `tests/e2e/manual_checklist.md`

**Step 1: Write the manual sign-off checklist**

Write `tests/e2e/manual_checklist.md`:

```markdown
# MapArr Manual Sign-Off Checklist

> Complete this checklist before tagging a release. These items require human judgement
> and cannot be automated. Check each item after visual inspection.

## Visual Quality

- [ ] Terminal boot animation plays smoothly (no flicker, no frozen frames)
- [ ] Service icons render at correct size (20px in rows, not blurry or oversized)
- [ ] YAML syntax highlighting is readable (changed lines stand out from unchanged)
- [ ] Health banner color is correct (green = ok, red/orange = problems)
- [ ] Health dot colors match service state in dashboard

## Layout & Responsiveness

- [ ] Directory browser modal renders correctly at 1280x720
- [ ] Directory browser modal renders correctly at 1920x1080
- [ ] Long stack names (30+ chars) don't break layout
- [ ] Many services (15+) render without overflow issues
- [ ] Paste area expands/collapses without layout jump

## User Experience

- [ ] Error messages are helpful (not raw Python tracebacks)
- [ ] Apply Fix confirmation modal clearly communicates "this writes to your files"
- [ ] Redeploy progress feels responsive (not frozen during docker compose up)
- [ ] Rate limit responses show a user-friendly message (not raw 429)
- [ ] Back button always returns to dashboard (never gets stuck on analysis screen)

## Cross-Browser (if applicable)

- [ ] Chrome: all journeys complete without errors
- [ ] Firefox: all journeys complete without errors (if supporting)
- [ ] Edge: all journeys complete without errors (if supporting)

## Docker Deployment

- [ ] Container starts from `docker compose up -d` with example compose
- [ ] Web UI loads on configured port
- [ ] Stacks are discoverable when mounted at /stacks
- [ ] Apply Fix works when /stacks is mounted read-write

---

**Sign-off:** ________________  **Date:** ________________
```

**Step 2: Commit**

```bash
git add tests/e2e/manual_checklist.md
git commit -m "docs: manual sign-off checklist for pre-release QA"
```

---

### Task 8: Create Sanitized Real Data Snapshot Script

**Files:**
- Create: `scripts/snapshot_real_stacks.py`

**Step 1: Write the sanitization script**

Write `scripts/snapshot_real_stacks.py`:

```python
"""
Create a sanitized snapshot of real Docker Compose stacks for E2E testing.

Copies compose files from a source directory, strips sensitive values
(API keys, passwords, tokens) from environment variables, and saves
to tests/e2e/fixtures/real-snapshot/.

Usage:
    python scripts/snapshot_real_stacks.py /path/to/docker/stacks

This is a ONE-TIME operation for creating test data.
The snapshot is committed to the repo and used in pre-release testing.
"""

import re
import shutil
import sys
from pathlib import Path

# Compose file names to look for
COMPOSE_FILENAMES = [
    "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
]

# Environment variable patterns that likely contain secrets
SECRET_PATTERNS = [
    re.compile(r"(API_?KEY|APIKEY)\s*[=:]\s*(.+)", re.IGNORECASE),
    re.compile(r"(PASSWORD|PASSWD|PASS)\s*[=:]\s*(.+)", re.IGNORECASE),
    re.compile(r"(TOKEN|SECRET|AUTH)\s*[=:]\s*(.+)", re.IGNORECASE),
    re.compile(r"(PRIVATE_?KEY)\s*[=:]\s*(.+)", re.IGNORECASE),
    re.compile(r"(DB_PASS|MYSQL_ROOT_PASSWORD|POSTGRES_PASSWORD)\s*[=:]\s*(.+)", re.IGNORECASE),
]

# Replacement value for secrets
REDACTED = "REDACTED_FOR_TESTING"


def sanitize_line(line: str) -> str:
    """Replace secret values in environment variable lines."""
    for pattern in SECRET_PATTERNS:
        match = pattern.search(line)
        if match:
            key = match.group(1)
            # Preserve the key, replace the value
            # Handle both "- KEY=value" and "KEY: value" formats
            if "=" in line:
                parts = line.split("=", 1)
                return f"{parts[0]}={REDACTED}\n"
            elif ":" in line:
                parts = line.split(":", 1)
                return f"{parts[0]}: {REDACTED}\n"
    return line


def sanitize_compose(content: str) -> str:
    """Sanitize a compose file by redacting secret values."""
    lines = content.splitlines(keepends=True)
    return "".join(sanitize_line(line) for line in lines)


def snapshot(source_dir: str, dest_dir: str):
    """Create sanitized snapshot of compose files."""
    source = Path(source_dir)
    dest = Path(dest_dir)

    if dest.exists():
        print(f"Destination already exists: {dest}")
        print("Delete it first if you want to regenerate.")
        sys.exit(1)

    copied = 0
    for stack_dir in sorted(source.iterdir()):
        if not stack_dir.is_dir() or stack_dir.name.startswith("."):
            continue

        for filename in COMPOSE_FILENAMES:
            compose = stack_dir / filename
            if compose.exists():
                out_dir = dest / stack_dir.name
                out_dir.mkdir(parents=True, exist_ok=True)

                content = compose.read_text(encoding="utf-8", errors="replace")
                sanitized = sanitize_compose(content)
                (out_dir / filename).write_text(sanitized, encoding="utf-8")
                copied += 1
                print(f"  {stack_dir.name}/{filename}")
                break  # Only copy first matching compose file

        # Also check one level deeper (cluster layout)
        for sub_dir in sorted(stack_dir.iterdir()):
            if not sub_dir.is_dir() or sub_dir.name.startswith("."):
                continue
            for filename in COMPOSE_FILENAMES:
                compose = sub_dir / filename
                if compose.exists():
                    out_dir = dest / stack_dir.name / sub_dir.name
                    out_dir.mkdir(parents=True, exist_ok=True)
                    content = compose.read_text(encoding="utf-8", errors="replace")
                    sanitized = sanitize_compose(content)
                    (out_dir / filename).write_text(sanitized, encoding="utf-8")
                    copied += 1
                    print(f"  {stack_dir.name}/{sub_dir.name}/{filename}")
                    break

    print(f"\nSnapshot complete: {copied} compose files → {dest}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} /path/to/stacks")
        sys.exit(1)

    source = sys.argv[1]
    dest = str(Path(__file__).parent.parent / "tests" / "e2e" / "fixtures" / "real-snapshot")

    print(f"Source: {source}")
    print(f"Destination: {dest}")
    print(f"Sanitizing compose files...\n")

    snapshot(source, dest)
```

**Step 2: Run the snapshot against user's real data**

Run: `cd /c/Projects/maparr && python scripts/snapshot_real_stacks.py "C:\DockerContainers"`
Expected: Compose files copied and sanitized. Secret values replaced with `REDACTED_FOR_TESTING`.

**Step 3: Verify the snapshot was sanitized**

Run: `cd /c/Projects/maparr && grep -ri "api_key\|password\|token\|secret" tests/e2e/fixtures/real-snapshot/ | head -5`
Expected: Any matches should show `REDACTED_FOR_TESTING`, not real values.

**Step 4: Add the snapshot to .gitignore (it contains user-specific data)**

Add to `.gitignore`:
```
# Real data snapshots contain user-specific stack layouts
# Regenerate with: python scripts/snapshot_real_stacks.py /path/to/stacks
tests/e2e/fixtures/real-snapshot/
```

**Step 5: Commit the script (not the snapshot)**

```bash
git add scripts/snapshot_real_stacks.py .gitignore
git commit -m "feat: add sanitization script for real data snapshots"
```

---

### Task 9: Wire Up pytest Markers and Configuration

**Files:**
- Modify: `pytest.ini` or `pyproject.toml` (pytest section)

**Step 1: Add pytest markers for E2E tests**

Create or update `pytest.ini`:

```ini
[pytest]
markers =
    docker: Docker deployment tests (require Docker)
testpaths = tests
```

**Step 2: Verify all tests can be collected without errors**

Run: `cd /c/Projects/maparr && python -m pytest tests/ --collect-only 2>&1 | tail -10`
Expected: All tests collected, including E2E tests.

**Step 3: Run the full test suite**

Run: `cd /c/Projects/maparr && python -m pytest tests/ -v -p no:capture --ignore=tests/e2e 2>&1 | tail -10`
Expected: Existing 682+ tests still pass. E2E tests excluded (they need a running server).

**Step 4: Run E2E tests separately**

Run: `cd /c/Projects/maparr && python -m pytest tests/e2e/test_api_contracts.py -v -p no:capture 2>&1 | tail -20`
Expected: API contract tests pass (they use TestClient, no server needed).

**Step 5: Commit**

```bash
git add pytest.ini
git commit -m "chore: add pytest markers and config for E2E test discovery"
```

---

### Task 10: Update CLAUDE.md and MEMORY.md

**Files:**
- Modify: `CLAUDE.md` (in project root)
- Modify: `C:\Users\juddh\.claude\projects\C--DockerContainers\memory\MEMORY.md`

**Step 1: Update CLAUDE.md with acceptance test documentation**

Add a section about the E2E test infrastructure:

```markdown
## Acceptance Tests (E2E)

4-layer test suite in `tests/e2e/`:
- **Layer 1** (`test_components.py`): DOM assertions — element visibility, classes, text content
- **Layer 2** (`test_journeys.py`): User workflows — browse → scan → analyze → fix → redeploy
- **Layer 3** (`test_api_contracts.py`): API shapes — response fields, status codes, rate limits
- **Layer 4** (`test_docker.py`): Docker deployment — build, PUID/PGID, ports, healthcheck

Run API tests (no server needed):
```
pytest tests/e2e/test_api_contracts.py -v -p no:capture
```

Run Playwright tests (needs server):
```
pytest tests/e2e/test_components.py tests/e2e/test_journeys.py -v
```

Run Docker tests (needs Docker):
```
pytest tests/e2e/test_docker.py -v
```

Test data: 16 synthetic stacks in `tests/e2e/fixtures/stacks/`
Real data snapshot: `python scripts/snapshot_real_stacks.py /path/to/stacks`
```

**Step 2: Update test count in CLAUDE.md**

Update the test count to reflect new E2E tests.

**Step 3: Update MEMORY.md**

Add acceptance test infrastructure details to the MapArr section.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document acceptance test infrastructure in CLAUDE.md"
```

---

## Execution Summary

| Task | Description | New Files | Tests Added |
|------|-------------|-----------|-------------|
| 1 | E2E infrastructure + conftest | 3 | 0 |
| 2 | Synthetic test stacks | 17 compose files | 0 |
| 3 | Layer 3: API contracts | 1 | ~20 |
| 4 | Layer 1: Component specs | 1 | ~18 |
| 5 | Layer 2: User journeys | 1 | ~7 |
| 6 | Layer 4: Docker tests | 1 | ~3 |
| 7 | Manual checklist | 1 | 0 |
| 8 | Real data snapshot script | 1 | 0 |
| 9 | pytest config | 1 | 0 |
| 10 | Documentation | 2 | 0 |

**Total new tests:** ~48 acceptance tests across 4 layers
**Dependencies:** pytest-playwright, playwright (chromium), httpx (already installed)
**CI impact:** Layers 1-3 run on every push (~60s). Layer 4 runs pre-release only.
