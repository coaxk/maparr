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
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy single-service download client
QBITTORRENT_YAML = """\
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy single-service media server
PLEX_YAML = """\
services:
  plex:
    image: lscr.io/linuxserver/plex:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# A healthy multi-service stack (sonarr + qbittorrent with shared mount)
HEALTHY_MULTI_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    volumes:
      - ./config/sonarr:/config
      - /mnt/nas/data:/data
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    volumes:
      - ./config/qbit:/config
      - /mnt/nas/data:/data
"""

# A broken multi-service stack (different host paths — hardlinks will fail)
BROKEN_MULTI_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    volumes:
      - ./config/sonarr:/config
      - /host/tv:/data/tv
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
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
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# SABnzbd with shared mount
SABNZBD_YAML = """\
services:
  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:latest
    volumes:
      - ./config:/config
      - /mnt/nas/data:/data
"""

# Sonarr with DIFFERENT mount (for conflict testing)
SONARR_CONFLICT_YAML = """\
services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    volumes:
      - ./config:/config
      - /different/path:/data
"""
