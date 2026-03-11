"""
Tests for MapArr — Apply Fix (auto-apply corrected YAML).

Covers:
  - /api/apply-fix endpoint
  - Backup creation (.bak file)
  - YAML validation before writing
  - Error handling (missing file, invalid YAML, write failures)
  - File content preservation and correctness
"""

import os
import textwrap

import pytest

from conftest import BROKEN_MULTI_YAML, HEALTHY_MULTI_YAML


# ═══════════════════════════════════════════
# API Tests: /api/apply-fix
# ═══════════════════════════════════════════

class TestApplyFix:
    """Apply corrected YAML to compose file."""

    @pytest.fixture(autouse=True)
    def _set_stacks_root(self, tmp_path, monkeypatch):
        """Apply-fix requires MAPARR_STACKS_PATH to be set (write boundary).
        Point it at tmp_path so test compose files pass the security check."""
        monkeypatch.setenv("MAPARR_STACKS_PATH", str(tmp_path))

    def test_apply_creates_backup(self, client, tmp_path):
        """Applying a fix should create a .bak backup."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        corrected = textwrap.dedent("""\
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
        """)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": corrected,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert data["backup_file"] == "docker-compose.yml.bak"

        # Backup should exist with original content
        backup = tmp_path / "docker-compose.yml.bak"
        assert backup.exists()
        assert BROKEN_MULTI_YAML in backup.read_text()

        # Original file should now have corrected content
        assert "/mnt/nas/data:/data" in compose.read_text()

    def test_apply_writes_corrected_yaml(self, client, tmp_path):
        """The compose file should contain the corrected YAML after apply."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        corrected = textwrap.dedent("""\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - /mnt/nas/data:/data
        """)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": corrected,
        })

        assert resp.status_code == 200
        content = compose.read_text()
        assert "/mnt/nas/data:/data" in content

    def test_apply_rejects_invalid_yaml(self, client, tmp_path):
        """Invalid YAML should be rejected before writing."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": "{{invalid yaml!!!!",
        })

        assert resp.status_code == 400
        # Original file should be unchanged
        assert compose.read_text() == BROKEN_MULTI_YAML

    def test_apply_rejects_yaml_without_services(self, client, tmp_path):
        """YAML without a services key should be rejected."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": "version: '3'\nnetworks:\n  default:\n",
        })

        assert resp.status_code == 400
        assert "services" in resp.json()["error"].lower()

    def test_apply_missing_file(self, client, tmp_path):
        """Nonexistent compose file (within stacks root) should return 400."""
        # Path must be within stacks root to pass security check, but not exist on disk
        missing = tmp_path / "ghost" / "docker-compose.yml"
        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(missing),
            "corrected_yaml": "services:\n  test:\n    image: test\n",
        })

        assert resp.status_code == 400

    def test_apply_missing_compose_path(self, client):
        """Empty compose_file_path should return 400."""
        resp = client.post("/api/apply-fix", json={
            "compose_file_path": "",
            "corrected_yaml": "services:\n  test:\n    image: test\n",
        })

        assert resp.status_code == 400

    def test_apply_missing_corrected_yaml(self, client, tmp_path):
        """Empty corrected_yaml should return 400."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": "",
        })

        assert resp.status_code == 400

    def test_apply_bad_json(self, client):
        """Malformed JSON body should return 400."""
        resp = client.post("/api/apply-fix", content="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_apply_response_shape(self, client, tmp_path):
        """Successful apply response has expected fields."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(BROKEN_MULTI_YAML)

        corrected = "services:\n  sonarr:\n    image: test\n    volumes:\n      - /data:/data\n"

        resp = client.post("/api/apply-fix", json={
            "compose_file_path": str(compose),
            "corrected_yaml": corrected,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "compose_file" in data
        assert "backup_file" in data
        assert "message" in data
