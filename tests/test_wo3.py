"""
Tests for MapArr — Solution Output.

Tests cover:
  - Solution YAML generation (copy-pasteable output)
  - Service role mapping for container paths
  - API response includes solution_yaml
  - Frontend serving (index.html with analysis sections)
  - Full flow: parse error → discover → analyze → solution
"""

import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.analyzer import (
    analyze_stack,
    _generate_solution_yaml,
    _get_recommended_container_path,
    _extract_services,
    ServiceInfo,
    Conflict,
)


client = TestClient(app)


# ─── Helper ───

def make_stack(compose_data: dict, env_vars: dict = None) -> str:
    tmpdir = tempfile.mkdtemp(prefix="maparr_test_")
    (Path(tmpdir) / "docker-compose.yml").write_text(
        yaml.dump(compose_data), encoding="utf-8"
    )
    if env_vars:
        lines = [f"{k}={v}" for k, v in env_vars.items()]
        (Path(tmpdir) / ".env").write_text("\n".join(lines), encoding="utf-8")
    return tmpdir


# ═══════════════════════════════════════════
# Solution YAML Generation
# ═══════════════════════════════════════════

class TestSolutionYaml:

    def test_generates_yaml_for_broken_stack(self):
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/data/tv", "./config/sonarr:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/host/downloads:/downloads", "./config/qbit:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert result.solution_yaml is not None
        assert "services:" in result.solution_yaml
        assert "sonarr:" in result.solution_yaml
        assert "qbittorrent:" in result.solution_yaml

    def test_no_yaml_for_healthy_stack(self):
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data", "./config:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/data:/data", "./config:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert result.solution_yaml is None

    def test_yaml_includes_config_mounts(self):
        """Solution YAML should preserve existing config volume mounts."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/data/tv", "./config/sonarr:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/host/downloads:/downloads", "./config/qbit:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert "/config" in result.solution_yaml

    def test_yaml_has_host_data_mount(self):
        """Solution YAML should include the unified /host/data mount."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/host/tv:/tv"],
                },
                "transmission": {
                    "image": "linuxserver/transmission",
                    "volumes": ["/host/downloads:/downloads"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert "/host/data:" in result.solution_yaml

    def test_yaml_includes_comments(self):
        """Solution YAML should have helpful comments."""
        compose = {
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/a:/tv"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["/b:/dl"]},
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert result.solution_yaml.startswith("#")


# ═══════════════════════════════════════════
# Recommended Container Paths
# ═══════════════════════════════════════════

class TestRecommendedPaths:

    def test_sonarr_gets_tv_path(self):
        svc = ServiceInfo(name="sonarr", role="arr")
        assert _get_recommended_container_path(svc) == "/data/media/tv"

    def test_radarr_gets_movies_path(self):
        svc = ServiceInfo(name="radarr", role="arr")
        assert _get_recommended_container_path(svc) == "/data/media/movies"

    def test_qbittorrent_gets_torrents_path(self):
        svc = ServiceInfo(name="qbittorrent", role="download_client")
        assert _get_recommended_container_path(svc) == "/data/torrents"

    def test_sabnzbd_gets_usenet_path(self):
        svc = ServiceInfo(name="sabnzbd", role="download_client")
        assert _get_recommended_container_path(svc) == "/data/usenet"

    def test_plex_gets_media_path(self):
        svc = ServiceInfo(name="plex", role="media_server")
        assert _get_recommended_container_path(svc) == "/data/media"

    def test_unknown_arr_gets_media_fallback(self):
        svc = ServiceInfo(name="myapp", role="arr")
        assert _get_recommended_container_path(svc) == "/data/media"

    def test_unknown_downloader_gets_torrents_fallback(self):
        svc = ServiceInfo(name="mydownloader", role="download_client")
        assert _get_recommended_container_path(svc) == "/data/torrents"


# ═══════════════════════════════════════════
# API Response Shape — Solution Output
# ═══════════════════════════════════════════

class TestAPIResponseShape:

    def test_analyze_returns_solution_yaml(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/a:/tv"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["/b:/dl"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        assert "solution_yaml" in data
        assert data["solution_yaml"] is not None
        assert "services:" in data["solution_yaml"]

    def test_healthy_stack_has_null_solution_yaml(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/data:/data"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["/data:/data"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        assert data["solution_yaml"] is None
        assert data["status"] == "healthy"

    def test_services_have_role_field(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/data:/data"]},
                "plex": {"image": "plexinc/pms-docker", "volumes": ["/data:/data"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        for svc in data["services"]:
            assert "role" in svc

    def test_conflicts_have_fix_field(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/a:/tv"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["/b:/dl"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        for conflict in data["conflicts"]:
            assert "fix" in conflict
            assert conflict["fix"] is not None


# ═══════════════════════════════════════════
# Full Flow Test
# ═══════════════════════════════════════════

class TestFullFlow:

    def test_parse_then_analyze(self):
        """Simulate the full user flow: parse error → analyze stack."""
        # Step 1: Parse error
        parse_resp = client.post("/api/parse-error", json={
            "error_text": "Sonarr - Import failed, path does not exist: /data/tv/ShowName"
        })
        assert parse_resp.status_code == 200
        parsed = parse_resp.json()
        assert parsed["service"] == "sonarr"

        # Step 2: Analyze stack
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/mnt/tv:/data/tv", "./config:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/mnt/downloads:/downloads", "./config:/config"],
                },
            }
        })
        analyze_resp = client.post("/api/analyze", json={
            "stack_path": stack,
            "error": parsed,
        })
        assert analyze_resp.status_code == 200
        result = analyze_resp.json()

        # Verify full response
        assert result["status"] == "conflicts_found"
        assert result["conflict_count"] > 0
        assert result["solution_yaml"] is not None
        assert result["fix_summary"] is not None
        assert any(c["fix"] is not None for c in result["conflicts"])

    def test_healthy_flow(self):
        """Full flow for a healthy stack — no conflicts."""
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data", "./config:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["/data:/data", "./config:/config"],
                },
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        result = resp.json()
        assert result["status"] == "healthy"
        assert result["solution_yaml"] is None
        assert "No path conflicts" in result["fix_summary"]


# ═══════════════════════════════════════════
# Frontend Serving
# ═══════════════════════════════════════════

class TestFrontend:

    def test_index_has_analysis_sections(self):
        resp = client.get("/")
        assert resp.status_code == 200
        text = resp.text
        assert "step-current-setup" in text
        assert "step-problem" in text
        assert "step-solution" in text
        assert "solution-yaml" in text
        assert "Copy to Clipboard" in text
        assert "step-next" in text
        assert "step-trash" in text

    def test_index_has_styles(self):
        resp = client.get("/static/styles.css")
        assert resp.status_code == 200
        assert "code-block" in resp.text
        assert "copy-btn" in resp.text
        assert "conflict-item" in resp.text

    def test_index_has_app_js(self):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "showAnalysisResult" in resp.text
        assert "copySolutionYaml" in resp.text
