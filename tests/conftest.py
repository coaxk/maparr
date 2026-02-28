"""
Shared test fixtures for MapArr test suite.

Consolidates helpers that were duplicated across test_wo1-5.
All test files can now use these fixtures without importing directly.
"""

import os
import tempfile
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── Session Cleanup ───

@pytest.fixture(autouse=True)
def _clear_session_pipeline():
    """Reset the in-memory pipeline cache between tests.

    Without this, tests that call /api/pipeline-scan pollute the shared
    _session dict, causing later tests to see 'healthy_pipeline' instead
    of 'healthy' status.
    """
    from backend.main import _session
    _session["pipeline"] = None
    yield
    _session["pipeline"] = None


# ─── App Client ───

@pytest.fixture
def client():
    """FastAPI TestClient for API integration tests."""
    from backend.main import app
    return TestClient(app)


# ─── Temporary Stack Helpers ───

@pytest.fixture
def make_stack(tmp_path):
    """
    Create a temporary stack directory with a compose file.

    Returns a function: make_stack(yaml_content, env_content=None, dirname="teststack")
    that creates the directory and returns its path.
    """
    def _make(yaml_content, env_content=None, dirname="teststack"):
        stack_dir = tmp_path / dirname
        stack_dir.mkdir(exist_ok=True)
        compose = stack_dir / "docker-compose.yml"
        compose.write_text(textwrap.dedent(yaml_content))
        if env_content:
            env_file = stack_dir / ".env"
            env_file.write_text(textwrap.dedent(env_content))
        return str(stack_dir)
    return _make


@pytest.fixture
def make_pipeline_dir(tmp_path):
    """
    Create a temporary root directory with multiple stack subdirectories.

    Returns a function: make_pipeline_dir(stacks_dict)
    where stacks_dict is {dirname: yaml_content, ...}

    Returns the root directory path.
    """
    def _make(stacks_dict):
        for dirname, yaml_content in stacks_dict.items():
            stack_dir = tmp_path / dirname
            stack_dir.mkdir(exist_ok=True)
            compose = stack_dir / "docker-compose.yml"
            compose.write_text(textwrap.dedent(yaml_content))
        return str(tmp_path)
    return _make


# ─── Common Compose YAML Strings ───

# A healthy single-service *arr stack
SONARR_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy single-service download client
QBITTORRENT_YAML = """\
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy single-service media server
PLEX_YAML = """\
services:
  plex:
    image: lscr.io/linuxserver/plex:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy multi-service stack (sonarr + qbittorrent with shared mount)
HEALTHY_MULTI_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/qbit:/config
      - /mnt/nas/data:/data
"""

# A broken multi-service stack (different host paths — hardlinks will fail)
BROKEN_MULTI_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/sonarr:/config
      - /host/tv:/data/tv
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/qbit:/config
      - /host/downloads:/downloads
"""

# A non-media stack (utility service)
UTILITY_YAML = """\
services:
  watchtower:
    image: containrrr/watchtower:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""

# Radarr with shared mount (for pipeline testing)
RADARR_YAML = """\
services:
  radarr:
    image: lscr.io/linuxserver/radarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# SABnzbd with shared mount
SABNZBD_YAML = """\
services:
  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# Sonarr with DIFFERENT mount (for conflict testing)
SONARR_CONFLICT_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /different/path:/data
"""

# ─── v1.5.0 Download Client YAML Constants ───

# aria2 download client
ARIA2_YAML = """\
services:
  aria2:
    image: p3terx/aria2-pro:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# Flood (rTorrent frontend)
FLOOD_YAML = """\
services:
  flood:
    image: jesec/flood:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# rdtclient (Real-Debrid)
RDTCLIENT_YAML = """\
services:
  rdtclient:
    image: rogerfar/rdtclient:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# ─── RPM Test YAML Constants ───

# qBittorrent with DIFFERENT host path (for RPM overlap tests)
QBIT_SEPARATE_YAML = """\
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /mnt/nas/downloads:/downloads
"""

# SABnzbd with DIFFERENT host path (for RPM impossible tests)
SABNZBD_DISJOINT_YAML = """\
services:
  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config:/config
      - /opt/usenet:/downloads
"""

# ─── Permissions Test YAML Constants ───

# Healthy permissions: all services share the same PUID/PGID
HEALTHY_PERMS_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
    volumes:
      - ./config/qbit:/config
      - /mnt/nas/data:/data
"""

# PUID/PGID mismatch: sonarr=1000, qbittorrent missing (defaults to 911)
PUID_MISMATCH_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=022
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - UMASK=002
    volumes:
      - ./config/qbit:/config
      - /mnt/nas/data:/data
"""

# Root execution: service runs as UID 0
ROOT_EXECUTION_YAML = """\
services:
  huntarr:
    image: lscr.io/linuxserver/huntarr:latest
    environment:
      - PUID=0
      - PGID=0
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
"""

# UMASK mismatch: sonarr=022, qbittorrent=002
UMASK_MISMATCH_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=022
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
    volumes:
      - ./config/qbit:/config
      - /mnt/nas/data:/data
"""

# Mixed image families: LSIO sonarr + jlesage jdownloader
MIXED_FAMILIES_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  jdownloader2:
    image: jlesage/jdownloader-2:latest
    environment:
      - USER_ID=568
      - GROUP_ID=568
    volumes:
      - ./config/jd2:/config
      - /mnt/nas/data:/data
"""

# Compose user: directive instead of env vars
COMPOSE_USER_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  seerr:
    image: sctx/overseerr:latest
    user: "1000:1000"
    volumes:
      - ./config/seerr:/config
      - /mnt/nas/data:/data
"""
