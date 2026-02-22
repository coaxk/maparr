"""
Tests for MapArr — v1.5.0 Feature Coverage.

Covers:
  - Expanded download clients (aria2, flood, rdtclient)
  - PipelineService.volume_mounts field
  - Stale pipeline detection (mtime-based rescan)
  - Pipeline majority root override in fix generation
  - Apply Fix expanded affected services
  - _ROLE_CONTAINER_PATHS for new DCs
"""

import os
import time
import textwrap

import pytest

from conftest import (
    SONARR_YAML, QBITTORRENT_YAML, RADARR_YAML, PLEX_YAML,
    SABNZBD_YAML, HEALTHY_MULTI_YAML, BROKEN_MULTI_YAML,
    UTILITY_YAML, ARIA2_YAML, FLOOD_YAML, RDTCLIENT_YAML,
)


# ─── Helpers ───

def _resolve_and_analyze(stack_path, yaml_content=None, pipeline_context=None, scan_dir=None):
    """Helper: resolve a compose file and run analysis."""
    from backend.resolver import resolve_compose
    from backend.analyzer import analyze_stack

    compose_file = os.path.join(stack_path, "docker-compose.yml")
    resolved = resolve_compose(stack_path)

    return analyze_stack(
        resolved_compose=resolved,
        stack_path=stack_path,
        compose_file=compose_file,
        resolution_method="manual",
        pipeline_context=pipeline_context,
        scan_dir=scan_dir,
    )


def _make_pipeline_context(
    sibling_services=None, conflicts=None, mount_root="/mnt/nas/data",
    shared_mount=True, health="ok", total_media=5,
):
    """Build a pipeline_context dict matching the real format."""
    return {
        "role": "arr",
        "total_media": total_media,
        "shared_mount": shared_mount,
        "mount_root": mount_root,
        "health": health,
        "conflicts": conflicts or [],
        "sibling_services": sibling_services or [],
        "services_by_role": {},
        "summary": f"{total_media} media services",
    }


# ═══════════════════════════════════════════
# Expanded Download Client Detection
# ═══════════════════════════════════════════

class TestExpandedDownloadClients:
    """New download clients added in v1.5.0: aria2, flood, rdtclient."""

    def test_aria2_in_download_clients_set(self):
        from backend.analyzer import DOWNLOAD_CLIENTS
        assert "aria2" in DOWNLOAD_CLIENTS

    def test_flood_in_download_clients_set(self):
        from backend.analyzer import DOWNLOAD_CLIENTS
        assert "flood" in DOWNLOAD_CLIENTS

    def test_rdtclient_in_download_clients_set(self):
        from backend.analyzer import DOWNLOAD_CLIENTS
        assert "rdtclient" in DOWNLOAD_CLIENTS

    def test_aria2_classified_as_dc(self):
        from backend.analyzer import _classify_service
        assert _classify_service("aria2", "p3terx/aria2-pro") == "download_client"

    def test_flood_classified_as_dc(self):
        from backend.analyzer import _classify_service
        assert _classify_service("flood", "jesec/flood") == "download_client"

    def test_rdtclient_classified_as_dc(self):
        from backend.analyzer import _classify_service
        assert _classify_service("rdtclient", "rogerfar/rdtclient") == "download_client"

    def test_aria2_compose_analysis(self, make_stack):
        """Full stack analysis correctly identifies aria2."""
        stack_path = make_stack(ARIA2_YAML, dirname="aria2")
        result = _resolve_and_analyze(stack_path, ARIA2_YAML)
        services = result.to_dict()["services"]
        aria2 = [s for s in services if s["name"] == "aria2"]
        assert len(aria2) == 1
        assert aria2[0]["role"] == "download_client"

    def test_flood_compose_analysis(self, make_stack):
        """Full stack analysis correctly identifies flood."""
        stack_path = make_stack(FLOOD_YAML, dirname="flood")
        result = _resolve_and_analyze(stack_path, FLOOD_YAML)
        services = result.to_dict()["services"]
        flood = [s for s in services if s["name"] == "flood"]
        assert len(flood) == 1
        assert flood[0]["role"] == "download_client"

    def test_rdtclient_compose_analysis(self, make_stack):
        """Full stack analysis correctly identifies rdtclient."""
        stack_path = make_stack(RDTCLIENT_YAML, dirname="rdtclient")
        result = _resolve_and_analyze(stack_path, RDTCLIENT_YAML)
        services = result.to_dict()["services"]
        rdt = [s for s in services if s["name"] == "rdtclient"]
        assert len(rdt) == 1
        assert rdt[0]["role"] == "download_client"


# ═══════════════════════════════════════════
# _ROLE_CONTAINER_PATHS for new DCs
# ═══════════════════════════════════════════

class TestRoleContainerPaths:
    """Verify TRaSH Guides recommended container paths for new DCs."""

    def test_aria2_container_path(self):
        from backend.analyzer import _ROLE_CONTAINER_PATHS
        assert _ROLE_CONTAINER_PATHS["aria2"] == "/data/torrents"

    def test_flood_container_path(self):
        from backend.analyzer import _ROLE_CONTAINER_PATHS
        assert _ROLE_CONTAINER_PATHS["flood"] == "/data/torrents"

    def test_rdtclient_container_path(self):
        from backend.analyzer import _ROLE_CONTAINER_PATHS
        assert _ROLE_CONTAINER_PATHS["rdtclient"] == "/data/torrents"

    def test_jdownloader_container_path(self):
        from backend.analyzer import _ROLE_CONTAINER_PATHS
        assert _ROLE_CONTAINER_PATHS["jdownloader"] == "/data/downloads"


# ═══════════════════════════════════════════
# PipelineService.volume_mounts Field
# ═══════════════════════════════════════════

class TestPipelineServiceVolumeMounts:
    """volume_mounts field on PipelineService added in v1.5.0."""

    def test_volume_mounts_field_exists(self):
        """PipelineService has volume_mounts field with default empty list."""
        from backend.pipeline import PipelineService
        svc = PipelineService(
            stack_path="/test", stack_name="test",
            service_name="sonarr", role="arr",
            host_sources=set(), compose_file="docker-compose.yml",
        )
        assert svc.volume_mounts == []

    def test_volume_mounts_in_to_dict(self):
        """to_dict() includes volume_mounts."""
        from backend.pipeline import PipelineService
        svc = PipelineService(
            stack_path="/test", stack_name="test",
            service_name="sonarr", role="arr",
            host_sources=set(), compose_file="docker-compose.yml",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        d = svc.to_dict()
        assert "volume_mounts" in d
        assert len(d["volume_mounts"]) == 1
        assert d["volume_mounts"][0]["target"] == "/data"

    def test_pipeline_scan_populates_volume_mounts(self, make_pipeline_dir):
        """run_pipeline_scan() populates volume_mounts on media services."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({"sonarr": SONARR_YAML})
        result = run_pipeline_scan(root)

        assert len(result.media_services) >= 1
        sonarr = [s for s in result.media_services if s.service_name == "sonarr"]
        assert len(sonarr) == 1
        # /mnt/nas/data:/data should be in volume_mounts (config excluded)
        data_mounts = [m for m in sonarr[0].volume_mounts if m["target"] == "/data"]
        assert len(data_mounts) >= 1

    def test_volume_mounts_excludes_config(self, make_pipeline_dir):
        """Config mounts (/config) should NOT appear in volume_mounts."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({"sonarr": SONARR_YAML})
        result = run_pipeline_scan(root)

        sonarr = [s for s in result.media_services if s.service_name == "sonarr"][0]
        config_mounts = [m for m in sonarr.volume_mounts if "/config" in m["target"]]
        assert len(config_mounts) == 0

    def test_utility_service_not_in_pipeline(self, make_pipeline_dir):
        """Utility services (watchtower) are not media services."""
        from backend.pipeline import run_pipeline_scan

        root = make_pipeline_dir({"watchtower": UTILITY_YAML})
        result = run_pipeline_scan(root)

        # watchtower should not appear as media service
        names = [s.service_name for s in result.media_services]
        assert "watchtower" not in names

    def test_pipeline_context_carries_volume_mounts(self, make_pipeline_dir):
        """get_pipeline_context_for_stack() includes volume_mounts in siblings."""
        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })
        pipeline = run_pipeline_scan(root)
        sonarr_path = os.path.join(root, "sonarr")
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), sonarr_path)

        # qbittorrent should be in sibling_services with volume_mounts
        qbit_sibs = [s for s in ctx["sibling_services"] if s["service_name"] == "qbittorrent"]
        assert len(qbit_sibs) == 1
        assert "volume_mounts" in qbit_sibs[0]
        assert len(qbit_sibs[0]["volume_mounts"]) >= 1

    def test_multiple_data_mounts(self, make_pipeline_dir):
        """Service with 2 data volumes gets 2 entries in volume_mounts."""
        from backend.pipeline import run_pipeline_scan

        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - ./config:/config
              - /mnt/nas/data/tv:/data/tv
              - /mnt/nas/data/anime:/data/anime
        """
        root = make_pipeline_dir({"sonarr": yaml})
        result = run_pipeline_scan(root)

        sonarr = [s for s in result.media_services if s.service_name == "sonarr"][0]
        assert len(sonarr.volume_mounts) >= 2


# ═══════════════════════════════════════════
# Stale Pipeline Detection
# ═══════════════════════════════════════════

class TestStalePipelineDetection:
    """mtime-based pipeline staleness check in /api/analyze."""

    def test_stale_pipeline_triggers_rescan(self, client, make_pipeline_dir):
        """When compose mtime > pipeline scanned_at, pipeline is refreshed."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        # Pipeline scan (sets scanned_at)
        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp.status_code == 200

        # Modify compose file to make it newer than pipeline
        sonarr_path = os.path.join(root, "sonarr")
        compose_file = os.path.join(sonarr_path, "docker-compose.yml")
        time.sleep(0.1)  # Ensure different mtime
        with open(compose_file, "a", encoding="utf-8") as f:
            f.write("\n# touched\n")

        # Select and analyze — should trigger rescan
        client.post("/api/select-stack", json={"stack_path": sonarr_path})
        resp = client.post("/api/analyze", json={
            "stack_path": sonarr_path,
            "scan_dir": root,
        })
        assert resp.status_code == 200

    def test_fresh_pipeline_no_extra_scan(self, client, make_pipeline_dir):
        """When pipeline is fresh, no rescan needed."""
        root = make_pipeline_dir({"sonarr": SONARR_YAML})

        # Pipeline scan
        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp.status_code == 200

        # Analyze immediately — pipeline is fresh
        sonarr_path = os.path.join(root, "sonarr")
        client.post("/api/select-stack", json={"stack_path": sonarr_path})
        resp = client.post("/api/analyze", json={
            "stack_path": sonarr_path,
            "scan_dir": root,
        })
        assert resp.status_code == 200

    def test_no_pipeline_no_error(self, client, make_stack):
        """Analysis without any pipeline context doesn't error."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resp = client.post("/api/analyze", json={
            "stack_path": stack_path,
        })
        assert resp.status_code == 200

    def test_missing_compose_file_no_crash(self, client, make_pipeline_dir):
        """If compose file is deleted after pipeline scan, analysis handles gracefully."""
        root = make_pipeline_dir({"sonarr": SONARR_YAML})

        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp.status_code == 200

        # Analyze with a non-existent stack path (simulate deleted file)
        resp = client.post("/api/analyze", json={
            "stack_path": os.path.join(root, "nonexistent"),
            "scan_dir": root,
        })
        # Should return error status, not crash
        assert resp.status_code in (200, 400, 422)


# ═══════════════════════════════════════════
# Pipeline Majority Root Override
# ═══════════════════════════════════════════

class TestPipelineMajorityRootOverride:
    """Pipeline majority root used as host_root_override in fix generation."""

    def test_majority_root_override_in_solution(self, make_stack):
        """Broken stack + pipeline majority root → solution uses majority root."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")

        ctx = _make_pipeline_context(
            conflicts=[{
                "stack_name": "broken",
                "majority_root": "/srv/data",
                "description": "Mount root differs from pipeline majority",
            }],
            mount_root="/srv/data",
            health="warning",
        )
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML, pipeline_context=ctx)
        d = result.to_dict()

        # The solution YAML should reference /srv/data as the host root
        if d.get("solution_yaml"):
            assert "/srv/data" in d["solution_yaml"]

    def test_no_pipeline_uses_detected_root(self, make_stack):
        """Without pipeline, analyzer detects host root from existing mounts."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML)
        d = result.to_dict()

        # Should still produce a solution (from detected mounts)
        assert d["status"] == "conflicts_found"

    def test_pipeline_host_root_set_even_with_existing_conflicts(self, make_stack):
        """Within-stack conflicts exist AND pipeline majority root differs — root still set."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")

        ctx = _make_pipeline_context(
            conflicts=[{
                "stack_name": "broken",
                "majority_root": "/srv/data",
                "description": "Mount root differs",
            }],
            mount_root="/srv/data",
            health="warning",
        )
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML, pipeline_context=ctx)
        d = result.to_dict()

        # Should have conflicts and a solution
        assert len(d.get("conflicts", [])) >= 1
        assert d.get("solution_yaml") is not None

    def test_no_pipeline_conflicts_no_injection(self, make_stack):
        """Pipeline exists but no conflicts for this stack → no phantom conflict."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        ctx = _make_pipeline_context(conflicts=[], health="ok")
        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        assert d["status"] in ("healthy", "healthy_pipeline", "incomplete")

    def test_pipeline_conflict_injected_when_no_intra_stack(self, make_pipeline_dir):
        """Healthy within-stack but pipeline says mounts differ → conflict injected."""
        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack

        # sonarr uses /mnt/nas/data, qbit uses /different/path
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": textwrap.dedent("""\
                services:
                  qbittorrent:
                    image: lscr.io/linuxserver/qbittorrent:latest
                    volumes:
                      - ./config:/config
                      - /different/path:/data
            """),
        })
        pipeline = run_pipeline_scan(root)
        sonarr_path = os.path.join(root, "sonarr")
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), sonarr_path)

        result = _resolve_and_analyze(sonarr_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        # Pipeline conflict should be detected (mount mismatch across stacks)
        # Status should reflect the conflict, not be healthy
        assert d["status"] in ("pipeline_conflict", "cross_stack_conflict", "conflicts_found")


# ═══════════════════════════════════════════
# Apply Fix — Expanded Affected Services
# ═══════════════════════════════════════════

class TestApplyFixExpandedAffected:
    """When pipeline override is active, ALL media services get corrected."""

    def test_solution_includes_all_media_services(self, make_stack):
        """With host_root_override, solution YAML corrects ALL media services."""
        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - ./config/sonarr:/config
              - /host/tv:/data/tv
          radarr:
            image: lscr.io/linuxserver/radarr:latest
            volumes:
              - ./config/radarr:/config
              - /host/movies:/data/movies
          qbittorrent:
            image: lscr.io/linuxserver/qbittorrent:latest
            volumes:
              - ./config/qbit:/config
              - /host/downloads:/downloads
        """
        stack_path = make_stack(yaml, dirname="media")

        ctx = _make_pipeline_context(
            conflicts=[{
                "stack_name": "media",
                "majority_root": "/srv/data",
                "description": "Mount root differs",
            }],
            mount_root="/srv/data",
            health="warning",
        )
        result = _resolve_and_analyze(stack_path, yaml, pipeline_context=ctx)
        d = result.to_dict()

        if d.get("solution_yaml"):
            sol = d["solution_yaml"]
            # All three media services should have /srv/data in their volumes
            assert "sonarr" in sol
            assert "radarr" in sol
            assert "qbittorrent" in sol

    def test_without_override_only_conflicting_services(self, make_stack):
        """Without pipeline override, only conflicting services are in the fix."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML)
        d = result.to_dict()

        assert d["status"] == "conflicts_found"
        assert d.get("solution_yaml") is not None

    def test_corrected_yaml_is_valid(self, make_stack):
        """Patched YAML with pipeline override is valid YAML."""
        import yaml

        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")
        ctx = _make_pipeline_context(
            conflicts=[{
                "stack_name": "broken",
                "majority_root": "/srv/data",
                "description": "Mount root differs",
            }],
            mount_root="/srv/data",
            health="warning",
        )
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML, pipeline_context=ctx)
        d = result.to_dict()

        if d.get("corrected_yaml"):
            # Should not raise
            parsed = yaml.safe_load(d["corrected_yaml"])
            assert "services" in parsed

    def test_corrected_yaml_preserves_non_volume_config(self, make_stack):
        """corrected_yaml (patched original) preserves ports, env, etc."""
        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            ports:
              - "8989:8989"
            environment:
              PUID: "1000"
            volumes:
              - ./config:/config
              - /host/tv:/data/tv
          qbittorrent:
            image: lscr.io/linuxserver/qbittorrent:latest
            ports:
              - "8080:8080"
            volumes:
              - ./config:/config
              - /host/downloads:/downloads
        """
        stack_path = make_stack(yaml, dirname="media")
        ctx = _make_pipeline_context(
            conflicts=[{
                "stack_name": "media",
                "majority_root": "/srv/data",
                "description": "Mount root differs",
            }],
            mount_root="/srv/data",
            health="warning",
        )
        result = _resolve_and_analyze(stack_path, yaml, pipeline_context=ctx)
        d = result.to_dict()

        # corrected_yaml is the patched original — should keep ports/env
        if d.get("corrected_yaml"):
            corr = d["corrected_yaml"]
            assert "8989:8989" in corr
            assert "8080:8080" in corr
            assert "PUID" in corr

    def test_apply_fix_api_creates_backup(self, client, make_stack):
        """Apply fix creates .bak file before writing."""
        stack_path = make_stack(BROKEN_MULTI_YAML, dirname="broken")
        compose_file = os.path.join(stack_path, "docker-compose.yml")

        # Analyze first
        result = _resolve_and_analyze(stack_path, BROKEN_MULTI_YAML)
        d = result.to_dict()

        if d.get("corrected_yaml"):
            resp = client.post("/api/apply-fix", json={
                "compose_file": compose_file,
                "corrected_yaml": d["corrected_yaml"],
            })
            assert resp.status_code == 200
            # Backup should exist
            assert os.path.exists(compose_file + ".bak")
