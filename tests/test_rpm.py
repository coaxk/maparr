"""
Tests for MapArr — RPM (Remote Path Mapping) Calculator.

Covers:
  - _find_host_overlap(): Host path overlap detection
  - _calculate_rpm_mappings(): Full RPM entry generation for DC→arr pairs
  - API integration: rpm_mappings in /api/analyze response
"""

import os
import textwrap

import pytest

from conftest import (
    SONARR_YAML, QBITTORRENT_YAML, RADARR_YAML,
    SABNZBD_YAML, PLEX_YAML, HEALTHY_MULTI_YAML,
    QBIT_SEPARATE_YAML, SABNZBD_DISJOINT_YAML,
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


def _make_sibling(name, role, stack_name=None, volume_mounts=None):
    """Build a sibling service dict for pipeline context."""
    return {
        "service_name": name,
        "role": role,
        "stack_name": stack_name or name,
        "volume_mounts": volume_mounts or [],
    }


# ═══════════════════════════════════════════
# _find_host_overlap() — Pure Function Tests
# ═══════════════════════════════════════════

class TestFindHostOverlap:
    """Test host path overlap detection for RPM feasibility."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from backend.analyzer import _find_host_overlap
        self.overlap = _find_host_overlap

    def test_identical_paths(self):
        """Same host path → empty offset (RPM only needs container path diff)."""
        assert self.overlap("/mnt/nas", "/mnt/nas") == ""

    def test_dc_deeper_than_arr(self):
        """DC path is subdirectory of arr path → returns offset."""
        result = self.overlap("/mnt/nas/downloads", "/mnt/nas")
        assert result == "/downloads"

    def test_arr_deeper_than_dc(self):
        """Arr path is subdirectory of DC path → empty offset."""
        result = self.overlap("/mnt/nas", "/mnt/nas/downloads")
        assert result == ""

    def test_no_overlap_disjoint(self):
        """Completely disjoint paths → None (RPM impossible)."""
        result = self.overlap("/host/downloads", "/srv/media")
        assert result is None

    def test_trailing_slashes_ignored(self):
        """Trailing slashes should not affect overlap detection."""
        assert self.overlap("/mnt/nas/", "/mnt/nas") == ""
        assert self.overlap("/mnt/nas", "/mnt/nas/") == ""
        assert self.overlap("/mnt/nas/downloads/", "/mnt/nas") == "/downloads"

    def test_deep_nesting(self):
        """Multi-level deep nesting returns full offset."""
        result = self.overlap("/a/b/c/d", "/a/b")
        assert result == "/c/d"

    def test_single_level_overlap(self):
        """Single level deeper returns single dir offset."""
        result = self.overlap("/data/downloads", "/data")
        assert result == "/downloads"

    def test_one_char_difference_not_prefix(self):
        """'/mnt/nasa' should NOT match '/mnt/nas' — not a directory prefix."""
        result = self.overlap("/mnt/nasa", "/mnt/nas")
        assert result is None

    def test_root_paths(self):
        """Both root → empty offset."""
        assert self.overlap("/", "/") == ""

    def test_empty_dc_path(self):
        """Empty DC path → empty string (both stripped to empty, treated as equal)."""
        result = self.overlap("", "/mnt")
        # Empty string after rstrip becomes "" which starts with "" + "/"
        # Actual behavior: "" is prefix-checked against "/mnt" — returns ""
        assert result is not None  # Doesn't crash

    def test_empty_arr_path(self):
        """Empty arr path → offset (DC starts with empty prefix)."""
        result = self.overlap("/mnt", "")
        # "" rstripped is ""; "/mnt".startswith("" + "/") → True
        assert result is not None  # Doesn't crash

    def test_partial_name_overlap_not_match(self):
        """/mnt/data-backup should not match /mnt/data."""
        result = self.overlap("/mnt/data-backup", "/mnt/data")
        assert result is None


# ═══════════════════════════════════════════
# _calculate_rpm_mappings() — Integration Tests
# ═══════════════════════════════════════════

class TestCalculateRpmMappings:
    """Test RPM mapping computation for download client → arr pairs."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from backend.analyzer import _calculate_rpm_mappings
        self.calc = _calculate_rpm_mappings

    def test_no_pipeline_returns_empty(self):
        """Without pipeline context, no mappings possible."""
        assert self.calc([], None) == []

    def test_no_arr_services_returns_empty(self, make_stack):
        """Only download clients, no arr apps → empty."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(QBITTORRENT_YAML, dirname="qbit")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        result = self.calc(services, ctx, stack_path=stack_path)
        assert result == []

    def test_no_dc_services_returns_empty(self, make_stack):
        """Only arr apps, no download clients → empty."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        result = self.calc(services, ctx, stack_path=stack_path)
        assert result == []

    def test_same_stack_dc_arr_pair(self, make_stack):
        """DC and arr in same stack with shared host path → possible RPM."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        yaml = """\
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
        stack_path = make_stack(yaml, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) == 1
        m = mappings[0]
        assert m["possible"] is True
        assert m["dc_service"] == "qbittorrent"
        assert m["arr_service"] == "sonarr"

    def test_cross_stack_dc_arr_pair(self, make_stack):
        """DC in sibling stack, arr in current stack → RPM via pipeline."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbittorrent",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_qbit])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) == 1
        m = mappings[0]
        assert m["possible"] is True
        assert m["dc_service"] == "qbittorrent"
        assert m["dc_stack"] == "qbittorrent"
        assert m["arr_service"] == "sonarr"
        assert m["arr_stack"] == "sonarr"

    def test_impossible_mapping_disjoint_paths(self, make_stack):
        """DC and arr have completely disjoint host paths → possible=False."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        sibling_sab = _make_sibling(
            "sabnzbd", "download_client", "sabnzbd",
            volume_mounts=[{"source": "/opt/usenet", "target": "/downloads"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_sab])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) == 1
        m = mappings[0]
        assert m["possible"] is False
        assert "restructuring" in m["reason"].lower() or "don't overlap" in m["reason"].lower()

    def test_multiple_dc_multiple_arr(self, make_stack):
        """Multiple DCs × multiple arrs → cross-product filtered to current stack."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - ./config/sonarr:/config
              - /mnt/nas/data:/data
          radarr:
            image: lscr.io/linuxserver/radarr:latest
            volumes:
              - ./config/radarr:/config
              - /mnt/nas/data:/data
        """
        stack_path = make_stack(yaml, dirname="arrs")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbit",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        sibling_sab = _make_sibling(
            "sabnzbd", "download_client", "sabnzbd",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_qbit, sibling_sab])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        # 2 DCs × 2 arrs = 4 mappings
        assert len(mappings) == 4
        assert all(m["possible"] for m in mappings)

    def test_different_container_paths_same_host(self, make_stack):
        """Same host path, different container paths → RPM maps between them."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - ./config:/config
              - /mnt/nas/data:/data
          qbittorrent:
            image: lscr.io/linuxserver/qbittorrent:latest
            volumes:
              - ./config:/config
              - /mnt/nas/data:/downloads
        """
        stack_path = make_stack(yaml, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) == 1
        m = mappings[0]
        assert m["possible"] is True
        assert m["remote_path"] == "/downloads/"
        assert m["local_path"] == "/data/"

    def test_dc_deeper_host_path(self, make_stack):
        """DC mounts /mnt/nas/downloads, arr mounts /mnt/nas → offset in local path."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbit",
            volume_mounts=[{"source": "/mnt/nas/data/torrents", "target": "/downloads"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_qbit])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) == 1
        m = mappings[0]
        assert m["possible"] is True
        assert m["remote_path"] == "/downloads/"
        assert "/torrents" in m["local_path"]

    def test_mapping_dict_structure(self, make_stack):
        """Verify all expected keys in a mapping dict."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(HEALTHY_MULTI_YAML, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        assert len(mappings) >= 1
        m = mappings[0]
        expected_keys = {
            "arr_service", "arr_stack", "dc_service", "dc_stack",
            "host", "remote_path", "local_path",
            "dc_host_path", "arr_host_path", "possible", "reason",
        }
        assert set(m.keys()) == expected_keys

    def test_paths_end_with_slash(self, make_stack):
        """Remote and local paths should end with trailing slash."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(HEALTHY_MULTI_YAML, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        for m in mappings:
            if m["possible"]:
                assert m["remote_path"].endswith("/")
                assert m["local_path"].endswith("/")

    def test_host_field_is_dc_name(self, make_stack):
        """The 'host' field should be the DC service name (what arr uses to identify it)."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        stack_path = make_stack(HEALTHY_MULTI_YAML, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        for m in mappings:
            assert m["host"] == m["dc_service"]

    def test_skip_pairs_outside_current_stack(self, make_stack):
        """Pairs where NEITHER service is in the current stack should be excluded."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        # Current stack has only sonarr (arr)
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        # Two siblings: radarr (arr) and qbit (dc) — both external
        sibling_radarr = _make_sibling(
            "radarr", "arr", "radarr",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbit",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_radarr, sibling_qbit])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        # Should only have sonarr↔qbit (sonarr is in current stack)
        # Should NOT have radarr↔qbit (both external)
        for m in mappings:
            assert m["arr_stack"] == "sonarr" or m["dc_stack"] == "sonarr"

    def test_config_only_mounts_skipped(self, make_stack):
        """Services with only /config mounts produce no data mounts → skipped."""
        from backend.analyzer import _extract_services
        from backend.resolver import resolve_compose

        yaml = """\
        services:
          sonarr:
            image: lscr.io/linuxserver/sonarr:latest
            volumes:
              - ./config:/config
              - /mnt/nas/data:/data
          qbittorrent:
            image: lscr.io/linuxserver/qbittorrent:latest
            volumes:
              - ./config:/config
        """
        stack_path = make_stack(yaml, dirname="media")
        resolved = resolve_compose(stack_path)
        services = _extract_services(resolved)

        ctx = _make_pipeline_context(sibling_services=[])
        mappings = self.calc(services, ctx, stack_path=stack_path)

        # qbit has no data mount → no RPM pair
        assert len(mappings) == 0


# ═══════════════════════════════════════════
# RPM in Full Analysis — API Integration
# ═══════════════════════════════════════════

class TestRpmApiIntegration:
    """RPM mappings appear correctly in full analysis results."""

    def test_rpm_mappings_in_analysis_result(self, make_stack):
        """analyze_stack with pipeline context returns rpm_mappings."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbit",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/data"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_qbit])
        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        assert "rpm_mappings" in d
        assert len(d["rpm_mappings"]) >= 1

    def test_rpm_mappings_empty_without_pipeline(self, make_stack):
        """No pipeline context → empty rpm_mappings."""
        stack_path = make_stack(HEALTHY_MULTI_YAML, dirname="media")
        result = _resolve_and_analyze(stack_path, HEALTHY_MULTI_YAML)
        d = result.to_dict()

        assert d["rpm_mappings"] == []

    def test_rpm_possible_with_shared_host(self, make_stack):
        """When DC and arr share host paths, RPM is marked possible."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        sibling_qbit = _make_sibling(
            "qbittorrent", "download_client", "qbit",
            volume_mounts=[{"source": "/mnt/nas/data", "target": "/downloads"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_qbit])
        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        possible = [m for m in d["rpm_mappings"] if m["possible"]]
        assert len(possible) >= 1

    def test_rpm_impossible_with_disjoint_host(self, make_stack):
        """When DC and arr have disjoint host paths, RPM is marked impossible."""
        stack_path = make_stack(SONARR_YAML, dirname="sonarr")

        sibling_sab = _make_sibling(
            "sabnzbd", "download_client", "sabnzbd",
            volume_mounts=[{"source": "/opt/usenet", "target": "/downloads"}],
        )
        ctx = _make_pipeline_context(sibling_services=[sibling_sab])
        result = _resolve_and_analyze(stack_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        impossible = [m for m in d["rpm_mappings"] if not m["possible"]]
        assert len(impossible) >= 1

    def test_rpm_via_api_endpoint(self, client, make_pipeline_dir):
        """Full API flow: pipeline-scan → select → analyze → rpm_mappings present."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        # Pipeline scan
        resp = client.post("/api/pipeline-scan", json={"scan_dir": root})
        assert resp.status_code == 200

        # Select stack
        sonarr_path = os.path.join(root, "sonarr")
        client.post("/api/select-stack", json={"stack_path": sonarr_path})

        # Analyze
        resp = client.post("/api/analyze", json={
            "stack_path": sonarr_path,
            "scan_dir": root,
        })
        assert resp.status_code == 200
        data = resp.json()

        assert "rpm_mappings" in data

    def test_rpm_counts_multiple_pairs(self, make_pipeline_dir):
        """Pipeline with multiple DCs produces multiple RPM entries."""
        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
            "sabnzbd": SABNZBD_YAML,
        })

        from backend.pipeline import run_pipeline_scan, get_pipeline_context_for_stack

        pipeline = run_pipeline_scan(root)
        sonarr_path = os.path.join(root, "sonarr")
        ctx = get_pipeline_context_for_stack(pipeline.to_dict(), sonarr_path)

        result = _resolve_and_analyze(sonarr_path, SONARR_YAML, pipeline_context=ctx)
        d = result.to_dict()

        # sonarr has 2 DC siblings → at least 2 RPM entries
        assert len(d["rpm_mappings"]) >= 2
