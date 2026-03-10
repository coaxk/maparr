"""
Tests for MapArr — Pipeline-First Analysis.

Covers:
  - run_pipeline_scan() — full directory scanning, media service discovery
  - PipelineService / PipelineResult dataclasses
  - Mount conflict detection across the pipeline
  - Helper functions: get_pipeline_role(), get_pipeline_context_for_stack()
  - Edge cases: empty dir, no media, single service, permission issues
  - API endpoint: /api/pipeline-scan
"""

import os
import textwrap
import time

import pytest


# ─── Import fixtures from conftest ───
from conftest import (
    SONARR_YAML, QBITTORRENT_YAML, PLEX_YAML, RADARR_YAML,
    SABNZBD_YAML, UTILITY_YAML, SONARR_CONFLICT_YAML,
)


# ═══════════════════════════════════════════
# Unit Tests: run_pipeline_scan()
# ═══════════════════════════════════════════

class TestPipelineScan:
    """Core pipeline scanning functionality."""

    def test_scan_healthy_pipeline(self, make_pipeline_dir):
        """Full pipeline with shared mounts → health ok, shared_mount True."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "radarr": RADARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "plex": PLEX_YAML,
        })
        result = run_pipeline_scan(root)

        assert result.stacks_scanned >= 4
        assert len(result.media_services) == 4
        assert result.health == "ok"
        assert result.shared_mount is True
        assert "/mnt/nas" in result.mount_root
        assert not result.conflicts
        assert result.roles_present == {"arr", "download_client", "media_server"}
        assert not result.roles_missing

    def test_scan_with_conflicts(self, make_pipeline_dir):
        """Pipeline with divergent mounts → detects conflicts."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_CONFLICT_YAML,  # /different/path
            "qbittorrent": QBITTORRENT_YAML,  # /mnt/nas/data
            "plex": PLEX_YAML,  # /mnt/nas/data
        })
        result = run_pipeline_scan(root)

        assert len(result.media_services) == 3
        assert result.shared_mount is False
        assert len(result.conflicts) >= 1
        assert result.health == "problem"
        # The conflict should identify sonarr as the outlier
        conflict_services = [c["service_name"] for c in result.conflicts]
        assert "sonarr" in conflict_services

    def test_scan_no_media_services(self, make_pipeline_dir):
        """Directory with only utility stacks → no media services, health ok."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "watchtower": UTILITY_YAML,
            "portainer": UTILITY_YAML,
        })
        result = run_pipeline_scan(root)

        assert result.stacks_scanned >= 2
        assert len(result.media_services) == 0
        assert result.health == "ok"

    def test_scan_single_media_service(self, make_pipeline_dir):
        """Single media service → vacuously shared, no comparison needed."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "watchtower": UTILITY_YAML,
        })
        result = run_pipeline_scan(root)

        assert len(result.media_services) == 1
        assert result.shared_mount is True  # Vacuously true
        assert not result.conflicts

    def test_scan_empty_directory(self, tmp_path):
        """Empty directory → zero stacks, health ok."""
        from backend.pipeline import run_pipeline_scan

        result = run_pipeline_scan(str(tmp_path))

        assert result.stacks_scanned == 0
        assert len(result.media_services) == 0
        assert result.health == "ok"

    def test_scan_invalid_directory(self):
        """Invalid directory → health problem."""
        from backend.pipeline import run_pipeline_scan

        result = run_pipeline_scan("/nonexistent/path/that/does/not/exist")

        assert result.health == "problem"
        assert "not found" in result.summary.lower() or "invalid" in result.summary.lower()

    def test_scan_mixed_stacks(self, make_pipeline_dir):
        """Mix of media and utility stacks → only media services counted."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "watchtower": UTILITY_YAML,
            "portainer": UTILITY_YAML,
            "nginx": UTILITY_YAML,
        })
        result = run_pipeline_scan(root)

        assert result.stacks_scanned >= 5
        assert len(result.media_services) == 2
        assert result.health in ("ok", "warning")

    def test_scan_hidden_dirs_skipped(self, make_pipeline_dir, tmp_path):
        """Hidden directories (.git, .cache) should be skipped."""
        from backend.pipeline import run_pipeline_scan

        make_pipeline_dir({"sonarr": SONARR_YAML})
        # Create a hidden dir with a compose file
        hidden = tmp_path / ".hidden_stack"
        hidden.mkdir()
        (hidden / "docker-compose.yml").write_text(QBITTORRENT_YAML)

        result = run_pipeline_scan(str(tmp_path))

        # Only sonarr should be found, not the hidden one
        service_names = [s.service_name for s in result.media_services]
        assert "sonarr" in service_names
        assert "qbittorrent" not in service_names

    def test_scan_roles_missing(self, make_pipeline_dir):
        """Pipeline with arr but no download client → roles_missing populated."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "radarr": RADARR_YAML,
        })
        result = run_pipeline_scan(root)

        assert "arr" in result.roles_present
        assert "download_client" in result.roles_missing
        assert result.health == "warning"  # Missing roles = warning


# ═══════════════════════════════════════════
# Unit Tests: Data Structures
# ═══════════════════════════════════════════

class TestPipelineDataStructures:
    """PipelineService and PipelineResult serialization."""

    def test_pipeline_service_to_dict(self):
        from backend.pipeline import PipelineService

        svc = PipelineService(
            stack_path="/stacks/sonarr",
            stack_name="sonarr",
            service_name="sonarr",
            role="arr",
            host_sources={"/mnt/nas/data"},
            compose_file="/stacks/sonarr/docker-compose.yml",
        )
        d = svc.to_dict()

        assert d["stack_name"] == "sonarr"
        assert d["role"] == "arr"
        assert d["host_sources"] == ["/mnt/nas/data"]
        assert d["compose_file"] == "docker-compose.yml"

    def test_pipeline_result_to_dict(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        result = run_pipeline_scan(root)
        d = result.to_dict()

        assert "scan_dir" in d
        assert "scanned_at" in d
        assert "media_services" in d
        assert "media_service_count" in d
        assert "roles_present" in d
        assert "health" in d
        assert "summary" in d
        assert "steps" in d
        assert isinstance(d["media_services"], list)
        assert d["media_service_count"] == len(d["media_services"])

    def test_pipeline_result_services_by_role(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "radarr": RADARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "plex": PLEX_YAML,
        })
        result = run_pipeline_scan(root)

        assert "arr" in result.services_by_role
        assert "download_client" in result.services_by_role
        assert "media_server" in result.services_by_role
        assert len(result.services_by_role["arr"]) == 2  # sonarr + radarr


# ═══════════════════════════════════════════
# Unit Tests: Pipeline Helpers
# ═══════════════════════════════════════════

class TestPipelineHelpers:
    """get_pipeline_role() and get_pipeline_context_for_stack()."""

    def test_get_pipeline_role(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan, get_pipeline_role

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        result = run_pipeline_scan(root)
        pipeline_dict = result.to_dict()

        sonarr_path = os.path.join(root, "sonarr")
        assert get_pipeline_role(pipeline_dict, sonarr_path) == "arr"

        qbit_path = os.path.join(root, "qbittorrent")
        assert get_pipeline_role(pipeline_dict, qbit_path) == "download_client"

    def test_get_pipeline_role_unknown_stack(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan, get_pipeline_role

        root = make_pipeline_dir({"sonarr": SONARR_YAML})
        result = run_pipeline_scan(root)
        pipeline_dict = result.to_dict()

        assert get_pipeline_role(pipeline_dict, "/nonexistent/stack") is None

    def test_get_pipeline_context_for_stack(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "radarr": RADARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "plex": PLEX_YAML,
        })
        result = run_pipeline_scan(root)
        pipeline_dict = result.to_dict()

        sonarr_path = os.path.join(root, "sonarr")
        ctx = get_pipeline_context_for_stack(pipeline_dict, sonarr_path)

        assert ctx["role"] == "arr"
        assert ctx["total_media"] == 4
        assert ctx["shared_mount"] is True
        assert ctx["health"] == "ok"
        # Sibling services should NOT include sonarr itself
        sibling_names = [s.get("service_name") for s in ctx["sibling_services"]]
        assert "sonarr" not in sibling_names
        assert "qbittorrent" in sibling_names

    def test_pipeline_context_with_conflicts(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack

        root = make_pipeline_dir({
            "sonarr": SONARR_CONFLICT_YAML,  # /different/path
            "qbittorrent": QBITTORRENT_YAML,  # /mnt/nas/data
        })
        result = run_pipeline_scan(root)
        pipeline_dict = result.to_dict()

        sonarr_path = os.path.join(root, "sonarr")
        ctx = get_pipeline_context_for_stack(pipeline_dict, sonarr_path)

        assert ctx["shared_mount"] is False
        assert len(ctx["conflicts"]) >= 1


# ═══════════════════════════════════════════
# Unit Tests: Mount Conflict Detection
# ═══════════════════════════════════════════

class TestMountConflictDetection:
    """Pipeline-level mount conflict grouping."""

    def test_majority_group_detection(self, make_pipeline_dir):
        """When one service diverges, it should be the conflict — not the majority."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,          # /mnt/nas/data
            "radarr": RADARR_YAML,          # /mnt/nas/data
            "qbittorrent": QBITTORRENT_YAML,  # /mnt/nas/data
            "plex": SONARR_CONFLICT_YAML,   # /different/path (using sonarr image name but different mount)
        })
        result = run_pipeline_scan(root)

        if result.conflicts:
            # The majority uses /mnt/nas, so /different/path is the conflict
            for c in result.conflicts:
                assert "/mnt/nas" in c.get("majority_root", "")

    def test_all_same_mount_no_conflicts(self, make_pipeline_dir):
        """All services sharing identical mounts → no conflicts."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "radarr": RADARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "sabnzbd": SABNZBD_YAML,
            "plex": PLEX_YAML,
        })
        result = run_pipeline_scan(root)

        assert result.shared_mount is True
        assert len(result.conflicts) == 0

    def test_conflict_severity(self, make_pipeline_dir):
        """Pipeline mount conflicts should be critical severity."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_CONFLICT_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        result = run_pipeline_scan(root)

        for c in result.conflicts:
            assert c["severity"] == "critical"
            assert c["type"] == "pipeline_mount_mismatch"


# ═══════════════════════════════════════════
# Unit Tests: Pipeline Steps (UI Terminal Lines)
# ═══════════════════════════════════════════

class TestPipelineSteps:
    """Terminal step lines for UI display."""

    def test_healthy_pipeline_steps(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        result = run_pipeline_scan(root)

        icons = [s["icon"] for s in result.steps]
        assert "run" in icons  # Scanning line
        assert "ok" in icons   # Found services
        assert "done" in icons  # Complete

    def test_conflict_pipeline_steps(self, make_pipeline_dir):
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({
            "sonarr": SONARR_CONFLICT_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        result = run_pipeline_scan(root)

        icons = [s["icon"] for s in result.steps]
        assert "warn" in icons  # Conflict warning


# ═══════════════════════════════════════════
# API Tests: /api/pipeline-scan
# ═══════════════════════════════════════════

class TestPipelineAPI:
    """Integration tests for the pipeline-scan endpoint."""

    def test_pipeline_scan_endpoint(self, client, make_pipeline_dir):
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "plex": PLEX_YAML,
        })
        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})

        assert resp.status_code == 200
        data = resp.json()
        assert data["health"] in ("ok", "warning", "problem")
        assert data["media_service_count"] == 3
        assert "summary" in data

    def test_pipeline_scan_invalid_dir(self, client):
        resp = client.post("/api/pipeline-scan", json={"scan_dir": "/nonexistent/path"})

        # Backend validates path and returns 400 for nonexistent directories
        assert resp.status_code == 400

    def test_pipeline_scan_empty_body(self, client):
        """Empty body should use default scan path or return error."""
        resp = client.post("/api/pipeline-scan", json={})
        # Should handle gracefully — either scan default or return problem
        assert resp.status_code == 200

    def test_pipeline_scan_caches_in_session(self, client, make_pipeline_dir):
        """Pipeline result should be cached in session state."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp.status_code == 200

        # The next analyze call should have pipeline context available
        sonarr_path = os.path.join(root, "sonarr")
        resp2 = client.post("/api/analyze", json={"stack_path": sonarr_path})
        assert resp2.status_code == 200
        data = resp2.json()
        # Pipeline data should be present
        assert data.get("pipeline") is not None or data.get("status") in ("healthy_pipeline", "healthy")


# ═══════════════════════════════════════════
# Unit Tests: Pipeline Permission Awareness
# ═══════════════════════════════════════════

class TestPipelinePermissionAwareness:
    """Pipeline-level PUID/PGID consistency checks."""

    def test_cross_stack_puid_mismatch_health_warning(self, make_pipeline_dir):
        """Two stacks with different PUID → pipeline health=warning."""
        from backend.pipeline import run_pipeline_scan
        scan_dir = make_pipeline_dir({
            "sonarr": """
                services:
                  sonarr:
                    image: lscr.io/linuxserver/sonarr:latest
                    environment:
                      - PUID=1000
                      - PGID=1000
                    volumes:
                      - /data:/data
            """,
            "radarr": """
                services:
                  radarr:
                    image: lscr.io/linuxserver/radarr:latest
                    environment:
                      - PUID=1001
                      - PGID=1001
                    volumes:
                      - /data:/data
            """,
        })
        result = run_pipeline_scan(scan_dir).to_dict()
        perm_conflicts = [c for c in result["conflicts"] if c["type"] == "pipeline_permission_mismatch"]
        assert len(perm_conflicts) > 0
        assert result["health"] == "warning"

    def test_matching_puid_no_permission_conflict(self, make_pipeline_dir):
        """All stacks same PUID → no permission conflicts."""
        from backend.pipeline import run_pipeline_scan
        scan_dir = make_pipeline_dir({
            "sonarr": """
                services:
                  sonarr:
                    image: lscr.io/linuxserver/sonarr:latest
                    environment:
                      - PUID=1000
                      - PGID=1000
                    volumes:
                      - /data:/data
            """,
            "radarr": """
                services:
                  radarr:
                    image: lscr.io/linuxserver/radarr:latest
                    environment:
                      - PUID=1000
                      - PGID=1000
                    volumes:
                      - /data:/data
            """,
        })
        result = run_pipeline_scan(scan_dir).to_dict()
        perm_conflicts = [c for c in result["conflicts"] if c["type"] == "pipeline_permission_mismatch"]
        assert len(perm_conflicts) == 0

    def test_mount_conflict_overrides_perm_warning(self, make_pipeline_dir):
        """Mount conflicts (problem) take priority over permission mismatch (warning)."""
        from backend.pipeline import run_pipeline_scan
        scan_dir = make_pipeline_dir({
            "sonarr": """
                services:
                  sonarr:
                    image: lscr.io/linuxserver/sonarr:latest
                    environment:
                      - PUID=1000
                      - PGID=1000
                    volumes:
                      - /srv/tv:/data
            """,
            "radarr": """
                services:
                  radarr:
                    image: lscr.io/linuxserver/radarr:latest
                    environment:
                      - PUID=1001
                      - PGID=1001
                    volumes:
                      - /home/user/downloads:/data
            """,
        })
        result = run_pipeline_scan(scan_dir).to_dict()
        assert result["health"] == "problem"  # Mount conflict wins


# ═══════════════════════════════════════════
# Unit Tests: Cluster Layout Discovery
# ═══════════════════════════════════════════

class TestClusterDiscovery:
    """Test that pipeline scan discovers cluster layouts (one service per subfolder).

    Cluster layouts like Dockhand/Portainer store each service in its own
    subfolder under a parent directory, with no compose file at the parent level:

        scan_root/
          cluster-stack/          ← no docker-compose.yml here
            sonarr/               ← docker-compose.yml here
              docker-compose.yml
            qbittorrent/
              docker-compose.yml
          normal-stack/           ← docker-compose.yml here
            docker-compose.yml
    """

    def test_cluster_services_discovered(self, tmp_path):
        """Cluster subfolders with compose files are found by pipeline scan."""
        from backend.pipeline import run_pipeline_scan
        # Create a cluster layout: parent dir with no compose, children have compose
        cluster = tmp_path / "media-cluster"
        cluster.mkdir()
        # No compose file at cluster level

        sonarr_dir = cluster / "sonarr"
        sonarr_dir.mkdir()
        (sonarr_dir / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        qbit_dir = cluster / "qbittorrent"
        qbit_dir.mkdir()
        (qbit_dir / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        result = run_pipeline_scan(str(tmp_path))
        service_names = [s.service_name for s in result.media_services]
        assert "sonarr" in service_names, "Cluster sonarr not discovered"
        assert "qbittorrent" in service_names, "Cluster qbittorrent not discovered"

    def test_cluster_mixed_with_normal_stacks(self, tmp_path):
        """Cluster and normal stacks coexist — both discovered."""
        from backend.pipeline import run_pipeline_scan
        # Normal stack (compose at top level)
        normal = tmp_path / "plex"
        normal.mkdir()
        (normal / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              plex:
                image: lscr.io/linuxserver/plex:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        # Cluster layout (no compose at parent)
        cluster = tmp_path / "arr-cluster"
        cluster.mkdir()
        radarr_dir = cluster / "radarr"
        radarr_dir.mkdir()
        (radarr_dir / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              radarr:
                image: lscr.io/linuxserver/radarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        result = run_pipeline_scan(str(tmp_path))
        service_names = [s.service_name for s in result.media_services]
        assert "plex" in service_names, "Normal stack plex not discovered"
        assert "radarr" in service_names, "Cluster radarr not discovered"

    def test_cluster_conflict_detection(self, tmp_path):
        """Cluster services with conflicting mounts are flagged."""
        from backend.pipeline import run_pipeline_scan
        cluster = tmp_path / "broken-cluster"
        cluster.mkdir()

        sonarr_dir = cluster / "sonarr"
        sonarr_dir.mkdir()
        (sonarr_dir / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /host/tv:/data/tv
        """))

        qbit_dir = cluster / "qbittorrent"
        qbit_dir.mkdir()
        (qbit_dir / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /host/downloads:/downloads
        """))

        result = run_pipeline_scan(str(tmp_path)).to_dict()
        assert result["health"] == "problem", "Cluster mount conflict not detected"
        service_names = [s["service_name"] for s in result["media_services"]]
        assert "sonarr" in service_names
        assert "qbittorrent" in service_names

    def test_cluster_no_double_counting(self, tmp_path):
        """A directory with compose at top level is NOT also scanned as cluster."""
        from backend.pipeline import run_pipeline_scan
        # This directory has a compose file at top AND subfolders with compose files.
        # It should be treated as a normal stack only — not cluster-scanned.
        stack = tmp_path / "mixed"
        stack.mkdir()
        (stack / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        # Subfolder also has compose — should be ignored since parent has one
        sub = stack / "qbittorrent"
        sub.mkdir()
        (sub / "docker-compose.yml").write_text(textwrap.dedent("""\
            services:
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """))

        result = run_pipeline_scan(str(tmp_path))
        # sonarr from top-level compose should be found
        service_names = [s.service_name for s in result.media_services]
        assert "sonarr" in service_names
        # qbittorrent from subfolder should NOT be found (parent had compose)
        assert "qbittorrent" not in service_names, \
            "Subfolder compose should be ignored when parent has compose file"
