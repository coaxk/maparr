"""
MapArr v1.0 - Backend Unit Tests
Tests for PathAnalyzer, ArrConfigDetector, Database, and API endpoints.
Run: pytest backend/tests.py -v
"""

import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Set DB path to temp before importing main
_tmpdir = tempfile.mkdtemp()
os.environ["MAPARR_DB"] = os.path.join(_tmpdir, "test_maparr.db")

# Patch Docker before importing main (so global DockerManager doesn't fail)
with patch("docker.DockerClient") as _mock_docker:
    _mock_docker.return_value.ping.side_effect = Exception("no docker in tests")
    from main import (
        PathAnalyzer,
        ArrConfigDetector,
        Database,
        ManualPathEntry,
        app,
    )

from fastapi.testclient import TestClient

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# FIXTURES - Mock container data
# ═══════════════════════════════════════════════════════════

def make_container(name, image="linuxserver/sonarr:latest", volumes=None, env_vars=None, is_arr=True):
    return {
        "id": name[:8],
        "name": name,
        "image": image,
        "status": "running",
        "volumes": volumes or {},
        "env_vars": env_vars or {},
        "labels": {},
        "is_arr_app": is_arr,
    }


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Platform Detection
# ═══════════════════════════════════════════════════════════

class TestPlatformDetection:
    def test_detect_windows(self):
        containers = [make_container("sonarr", volumes={"/tv": "C:\\Users\\media\\tv"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "windows"

    def test_detect_unraid(self):
        containers = [make_container("sonarr", volumes={"/tv": "/mnt/user/data/media/tv"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "unraid"

    def test_detect_synology(self):
        containers = [make_container("sonarr", volumes={"/tv": "/volume1/data/media/tv"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "synology"

    def test_detect_wsl2(self):
        containers = [make_container("sonarr", volumes={"/tv": "/mnt/c/data/media/tv"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "wsl2"

    def test_detect_linux(self):
        containers = [make_container("sonarr", volumes={"/tv": "/var/lib/docker/volumes/media"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "linux"

    def test_detect_docker_generic(self):
        containers = [make_container("sonarr", volumes={"/tv": "/data/media/tv"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "docker"

    def test_detect_unknown(self):
        containers = [make_container("sonarr", volumes={"/config": "/opt/sonarr/config"})]
        analyzer = PathAnalyzer(containers)
        assert analyzer.platform == "unknown"

    def test_manual_paths_influence_platform(self):
        containers = [make_container("sonarr", volumes={"/config": "/opt/config"})]
        manual = [{"host_path": "/mnt/user/data/tv", "container_path": "/tv"}]
        analyzer = PathAnalyzer(containers, manual_paths=manual)
        assert analyzer.platform == "unraid"


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Conflict Detection
# ═══════════════════════════════════════════════════════════

class TestConflictDetection:
    def test_no_conflicts_healthy(self):
        """Containers sharing identical sources for same destinations = no conflicts."""
        containers = [
            make_container("sonarr", volumes={"/data": "/data"}),
            make_container("radarr", volumes={"/data": "/data"}),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        high = [c for c in result["conflicts"] if c["severity"] == "high"]
        assert len(high) == 0
        assert result["summary"]["status"] == "healthy"

    def test_multiple_sources_conflict(self):
        containers = [
            make_container("sonarr", volumes={"/data": "/media/tv"}),
            make_container("radarr", volumes={"/data": "/media/movies"}),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        high = [c for c in result["conflicts"] if c["type"] == "multiple_sources"]
        assert len(high) == 1
        assert "sonarr" in high[0]["containers"]
        assert "radarr" in high[0]["containers"]
        assert "fix" in high[0]
        assert "suggested_source" in high[0]["fix"]

    def test_arr_path_mismatch(self):
        containers = [
            make_container("sonarr", volumes={"/data": "/data", "/tv": "/data/tv", "/config": "/opt/sonarr"}),
            make_container("radarr", volumes={"/config": "/opt/radarr"}),  # missing /data and /tv
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        mismatches = [c for c in result["conflicts"] if c["type"] == "arr_path_mismatch"]
        assert len(mismatches) >= 1
        assert mismatches[0]["container"] == "radarr"
        assert "fix" in mismatches[0]

    def test_conflict_fix_has_suggested_source(self):
        containers = [
            make_container("sonarr", volumes={"/downloads": "/mnt/downloads"}),
            make_container("radarr", volumes={"/downloads": "/data/downloads"}),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        high = [c for c in result["conflicts"] if c["type"] == "multiple_sources"]
        assert len(high) == 1
        # /data/downloads should be preferred
        assert high[0]["fix"]["suggested_source"] == "/data/downloads"

    def test_manual_paths_create_conflicts(self):
        containers = [make_container("sonarr", volumes={"/data": "/data"})]
        manual = [{"container_name": "manual-radarr", "host_path": "/other/data", "container_path": "/data"}]
        analyzer = PathAnalyzer(containers, manual_paths=manual)
        result = analyzer.analyze()
        high = [c for c in result["conflicts"] if c["type"] == "multiple_sources"]
        assert len(high) == 1


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Hardlink Detection
# ═══════════════════════════════════════════════════════════

class TestHardlinkDetection:
    def test_hardlink_broken_detected(self):
        containers = [
            make_container("sonarr", volumes={"/tv": "/media/tv"}, is_arr=True),
            make_container("qbittorrent", image="qbittorrent:latest",
                          volumes={"/downloads": "/downloads"}, is_arr=False),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        hardlink = [c for c in result["conflicts"] if c["type"] == "hardlink_broken"]
        assert len(hardlink) == 1
        assert "fix" in hardlink[0]

    def test_hardlink_ok_shared_root(self):
        """Both containers share /data/media as their 2-level root -> hardlinks work."""
        containers = [
            make_container("sonarr", volumes={"/tv": "/data/media/tv"}, is_arr=True),
            make_container("qbittorrent", image="qbittorrent:latest",
                          volumes={"/downloads": "/data/media/downloads"}, is_arr=False),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        hardlink = [c for c in result["conflicts"] if c["type"] == "hardlink_broken"]
        assert len(hardlink) == 0


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Permission Detection
# ═══════════════════════════════════════════════════════════

class TestPermissionDetection:
    def test_missing_puid_pgid(self):
        containers = [make_container("sonarr", env_vars={}, is_arr=True)]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        perms = [c for c in result["conflicts"] if c["type"] == "permission_warning"]
        assert len(perms) == 1
        assert "PUID/PGID" in perms[0]["note"]

    def test_mismatched_puid_pgid(self):
        containers = [
            make_container("sonarr", env_vars={"PUID": "1000", "PGID": "1000"}, is_arr=True),
            make_container("radarr", env_vars={"PUID": "1001", "PGID": "1000"}, is_arr=True),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        mismatch = [c for c in result["conflicts"] if c["type"] == "permission_mismatch"]
        assert len(mismatch) == 1

    def test_consistent_puid_pgid_ok(self):
        containers = [
            make_container("sonarr", env_vars={"PUID": "1000", "PGID": "1000"}, is_arr=True),
            make_container("radarr", env_vars={"PUID": "1000", "PGID": "1000"}, is_arr=True),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        mismatch = [c for c in result["conflicts"] if c["type"] == "permission_mismatch"]
        assert len(mismatch) == 0


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Platform Recommendations
# ═══════════════════════════════════════════════════════════

class TestPlatformRecommendations:
    def test_windows_recommendations(self):
        containers = [make_container("sonarr", volumes={"/tv": "C:\\media\\tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        titles = [r["title"] for r in result["recommendations"]]
        assert "WSL2 Path Conversion" in titles

    def test_unraid_recommendations(self):
        containers = [make_container("sonarr", volumes={"/tv": "/mnt/user/data/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        titles = [r["title"] for r in result["recommendations"]]
        assert "Use /mnt/user for Hardlinks" in titles

    def test_synology_recommendations(self):
        containers = [make_container("sonarr", volumes={"/tv": "/volume1/data/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        titles = [r["title"] for r in result["recommendations"]]
        assert "Synology Volume Paths" in titles

    def test_unknown_platform_recommendation(self):
        containers = [make_container("sonarr", volumes={"/config": "/opt/sonarr"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        titles = [r["title"] for r in result["recommendations"]]
        assert "Platform Not Detected" in titles


# ═══════════════════════════════════════════════════════════
# PathAnalyzer - Hardlink Layout Suggestion
# ═══════════════════════════════════════════════════════════

class TestHardlinkLayout:
    def test_standard_layout(self):
        containers = [make_container("sonarr", volumes={"/data": "/data/media/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        layout = result["hardlink_layout"]
        assert "/data" in layout["structure"]

    def test_unraid_layout(self):
        containers = [make_container("sonarr", volumes={"/tv": "/mnt/user/data/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        layout = result["hardlink_layout"]
        assert "/mnt/user/data" in layout["structure"]

    def test_synology_layout(self):
        containers = [make_container("sonarr", volumes={"/tv": "/volume1/data/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        layout = result["hardlink_layout"]
        assert "/volume1/data" in layout["structure"]


# ═══════════════════════════════════════════════════════════
# ArrConfigDetector
# ═══════════════════════════════════════════════════════════

class TestArrConfigDetector:
    def test_detect_sonarr(self):
        containers = [
            make_container("sonarr", image="linuxserver/sonarr:latest",
                          volumes={"/tv": "/data/media/tv", "/config": "/opt/sonarr", "/downloads": "/data/downloads"},
                          is_arr=True),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert len(configs) == 1
        assert configs[0]["app_type"] == "sonarr"
        assert configs[0]["detected_root_folder"] == "/data/media/tv"
        assert len(configs[0]["download_paths"]) == 1

    def test_detect_radarr(self):
        containers = [
            make_container("radarr", image="linuxserver/radarr:latest",
                          volumes={"/movies": "/data/media/movies", "/config": "/opt/radarr"},
                          is_arr=True),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert len(configs) == 1
        assert configs[0]["app_type"] == "radarr"

    def test_missing_root_folder_issue(self):
        containers = [
            make_container("sonarr", image="linuxserver/sonarr:latest",
                          volumes={"/config": "/opt/sonarr"},
                          is_arr=True),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert len(configs) == 1
        assert any("root folder" in i.lower() for i in configs[0]["issues"])

    def test_missing_download_path_issue(self):
        containers = [
            make_container("sonarr", image="linuxserver/sonarr:latest",
                          volumes={"/tv": "/data/tv", "/config": "/opt/sonarr"},
                          is_arr=True),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert any("download" in i.lower() for i in configs[0]["issues"])

    def test_non_arr_skipped(self):
        containers = [
            make_container("nginx", image="nginx:latest", volumes={"/html": "/var/www"}, is_arr=False),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert len(configs) == 0

    def test_config_path_detection(self):
        containers = [
            make_container("radarr", image="radarr:latest",
                          volumes={"/config": "/opt/radarr", "/movies": "/data/movies"},
                          is_arr=True),
        ]
        configs = ArrConfigDetector.detect_arr_configs(containers)
        assert configs[0]["config_path"] == "/opt/radarr"


# ═══════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════

class TestDatabase:
    def setup_method(self):
        self.db = Database(os.path.join(tempfile.mkdtemp(), "test.db"))

    def test_save_and_get_analysis(self):
        analysis = {
            "platform": "linux",
            "summary": {"containers_analyzed": 5, "conflicts_found": 1},
        }
        aid = self.db.save_analysis(analysis)
        assert aid > 0

        retrieved = self.db.get_analysis(aid)
        assert retrieved is not None
        assert retrieved["platform"] == "linux"
        assert retrieved["result"]["platform"] == "linux"

    def test_list_analyses(self):
        for i in range(5):
            self.db.save_analysis({"platform": f"p{i}", "summary": {"containers_analyzed": i, "conflicts_found": 0}})
        analyses = self.db.get_analyses(limit=3)
        assert len(analyses) == 3

    def test_get_nonexistent_analysis(self):
        assert self.db.get_analysis(9999) is None

    def test_save_and_get_mapping(self):
        mapping = {"source": "/data", "dest": "/media"}
        mid = self.db.save_mapping(mapping, notes="test mapping")
        assert mid > 0

        mappings = self.db.get_mappings()
        assert len(mappings) == 1
        assert mappings[0]["mapping"]["source"] == "/data"
        assert mappings[0]["notes"] == "test mapping"

    def test_save_and_get_manual_path(self):
        entry = ManualPathEntry(
            container_name="sonarr",
            host_path="/data/tv",
            container_path="/tv",
            platform="linux",
        )
        pid = self.db.save_manual_path(entry)
        assert pid > 0

        paths = self.db.get_manual_paths()
        assert len(paths) == 1
        assert paths[0]["container_name"] == "sonarr"

    def test_delete_manual_path(self):
        entry = ManualPathEntry(
            container_name="test",
            host_path="/tmp",
            container_path="/tmp",
        )
        pid = self.db.save_manual_path(entry)
        assert self.db.delete_manual_path(pid) is True
        assert len(self.db.get_manual_paths()) == 0

    def test_delete_nonexistent_path(self):
        assert self.db.delete_manual_path(9999) is False


# ═══════════════════════════════════════════════════════════
# API Endpoints (no Docker required)
# ═══════════════════════════════════════════════════════════

class TestAPIEndpoints:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"

    def test_docker_status_disconnected(self):
        resp = client.get("/api/docker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False

    def test_containers_without_docker(self):
        resp = client.get("/api/containers")
        assert resp.status_code == 503

    def test_analyze_without_docker(self):
        resp = client.post("/api/analyze")
        assert resp.status_code == 503

    def test_recommendations_without_docker(self):
        resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommendations"][0]["priority"] == "critical"

    def test_manual_path_crud(self):
        # Create
        resp = client.post("/api/manual-paths", json={
            "container_name": "sonarr",
            "host_path": "/data/tv",
            "container_path": "/tv",
            "platform": "linux",
        })
        assert resp.status_code == 200
        path_id = resp.json()["id"]

        # List
        resp = client.get("/api/manual-paths")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        # Delete
        resp = client.delete(f"/api/manual-paths/{path_id}")
        assert resp.status_code == 200

        # Delete nonexistent
        resp = client.delete("/api/manual-paths/99999")
        assert resp.status_code == 404

    def test_manual_path_batch(self):
        resp = client.post("/api/manual-paths/batch", json={
            "entries": [
                {"container_name": "sonarr", "host_path": "/data/tv", "container_path": "/tv"},
                {"container_name": "radarr", "host_path": "/data/movies", "container_path": "/movies"},
            ],
            "platform": "linux",
        })
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_save_mapping(self):
        resp = client.post("/api/save-mapping", json={
            "source": "/data",
            "destination": "/media",
            "notes": "test",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    def test_list_mappings(self):
        resp = client.get("/api/mappings")
        assert resp.status_code == 200
        assert "mappings" in resp.json()

    def test_list_analyses(self):
        resp = client.get("/api/analyses")
        assert resp.status_code == 200
        assert "analyses" in resp.json()

    def test_get_analysis_not_found(self):
        resp = client.get("/api/analyses/99999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_containers_list(self):
        analyzer = PathAnalyzer([])
        result = analyzer.analyze()
        assert result["summary"]["containers_analyzed"] == 0
        assert result["summary"]["status"] == "healthy"

    def test_container_with_no_volumes(self):
        containers = [make_container("sonarr", volumes={})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        assert result["summary"]["containers_analyzed"] == 1

    def test_single_container_no_arr_consistency_check(self):
        containers = [make_container("sonarr", volumes={"/data": "/data"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        mismatches = [c for c in result["conflicts"] if c["type"] == "arr_path_mismatch"]
        assert len(mismatches) == 0

    def test_platform_override_via_hint(self):
        containers = [make_container("sonarr", volumes={"/data": "/data/tv"})]
        analyzer = PathAnalyzer(containers)
        analyzer.platform = "unraid"  # Override
        result = analyzer.analyze()
        assert result["platform"] == "unraid"
        titles = [r["title"] for r in result["recommendations"]]
        assert "Use /mnt/user for Hardlinks" in titles

    def test_many_containers(self):
        containers = [
            make_container(f"app{i}", volumes={f"/vol{i}": f"/host/vol{i}"}, is_arr=(i < 5))
            for i in range(20)
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        assert result["summary"]["containers_analyzed"] == 20

    def test_duplicate_volume_destinations_same_source(self):
        """Same source to same destination across containers = OK (sharing)."""
        containers = [
            make_container("sonarr", volumes={"/data": "/shared/data"}),
            make_container("radarr", volumes={"/data": "/shared/data"}),
        ]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        high = [c for c in result["conflicts"] if c["type"] == "multiple_sources"]
        assert len(high) == 0

    def test_special_characters_in_paths(self):
        containers = [make_container("sonarr", volumes={"/tv": "/data/My Media (2024)/tv"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        assert result["summary"]["containers_analyzed"] == 1

    def test_analyze_produces_analyzed_at_timestamp(self):
        containers = [make_container("sonarr", volumes={"/data": "/data"})]
        analyzer = PathAnalyzer(containers)
        result = analyzer.analyze()
        assert "analyzed_at" in result


# ═══════════════════════════════════════════════════════════
# DOCUMENTED EDGE CASES
# ═══════════════════════════════════════════════════════════
#
# 1. Docker socket not mounted: All container-dependent endpoints return 503.
#    The /recommendations endpoint gracefully returns a "connect docker" message.
#
# 2. No containers running: /api/analyze returns {"status": "no_data"} instead of error.
#
# 3. Windows backslash paths: Detected as "windows" platform. WSL2 /mnt/c/ detected as "wsl2".
#
# 4. Mixed platform paths: The first matching heuristic wins (windows > unraid > synology > wsl2 > linux).
#
# 5. Manual paths with no Docker: Manual paths alone can be analyzed via the manual-paths endpoints,
#    but /api/analyze requires Docker connection. Future enhancement: analyze manual paths standalone.
#
# 6. Large number of containers (20+): No performance degradation in analysis - all O(n) or O(n^2)
#    on small sets (container count, not file count).
#
# 7. Containers with no volumes: Handled gracefully, just no volume-related conflicts.
#
# 8. Permission env vars with UID vs PUID: Both checked (PUID preferred, UID as fallback).
#
# 9. Download client naming: Matches qbit*, transmission, deluge, nzbget, sabnzbd, rtorrent.
#    Custom-named download clients won't be detected for hardlink analysis.
#
# 10. Conflict resolution with stale data: resolve-conflict re-runs analysis fresh,
#     so conflict indices may shift if containers changed between analyze and resolve.
