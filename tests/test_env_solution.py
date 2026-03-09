"""
Tests for solution YAML category gating.

Ensures _generate_solution_yaml() only processes Category A (path) conflicts.
Permission-only or other non-path conflicts should NOT produce volume
restructure YAML.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from backend.analyzer import (
    analyze_stack,
    _generate_solution_yaml,
    ServiceInfo,
    Conflict,
    VolumeMount,
)


# ─── Helpers ───

def make_stack(compose_data: dict, env_vars: dict = None) -> str:
    tmpdir = tempfile.mkdtemp(prefix="maparr_env_sol_")
    (Path(tmpdir) / "docker-compose.yml").write_text(
        yaml.dump(compose_data), encoding="utf-8"
    )
    if env_vars:
        lines = [f"{k}={v}" for k, v in env_vars.items()]
        (Path(tmpdir) / ".env").write_text("\n".join(lines), encoding="utf-8")
    return tmpdir


def _make_service(name, image, role, volumes_raw):
    """Build a ServiceInfo with parsed volumes."""
    vols = []
    for raw in volumes_raw:
        parts = raw.split(":")
        ro = len(parts) > 2 and parts[2] == "ro"
        vols.append(VolumeMount(
            raw=raw,
            source=parts[0],
            target=parts[1] if len(parts) > 1 else parts[0],
            read_only=ro,
            is_named_volume=False,
        ))
    return ServiceInfo(name=name, image=image, role=role, volumes=vols)


# ═══════════════════════════════════════════
# Solution YAML Category Gating
# ═══════════════════════════════════════════

class TestSolutionYamlGating:

    def test_permission_only_no_volume_yaml(self):
        """Permission-only conflicts must NOT produce volume solution YAML."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"]),
            _make_service("qbittorrent", "linuxserver/qbittorrent", "download_client", ["/data:/data"]),
        ]
        # Only permission conflicts — no path conflicts
        conflicts = [
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "qbittorrent"],
                description="PUID/PGID mismatch between services",
            ),
        ]
        yaml_out, changed = _generate_solution_yaml(conflicts, services)
        assert yaml_out is None
        assert changed == []

    def test_tz_mismatch_only_no_volume_yaml(self):
        """TZ mismatch (Category B) must NOT produce volume solution YAML."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"]),
            _make_service("radarr", "linuxserver/radarr", "arr", ["/data:/data"]),
        ]
        conflicts = [
            Conflict(
                conflict_type="tz_mismatch",
                severity="medium",
                services=["sonarr", "radarr"],
                description="TZ mismatch",
            ),
        ]
        yaml_out, changed = _generate_solution_yaml(conflicts, services)
        assert yaml_out is None
        assert changed == []

    def test_infrastructure_only_no_volume_yaml(self):
        """Infrastructure conflicts (Category C) must NOT produce volume YAML."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"]),
        ]
        conflicts = [
            Conflict(
                conflict_type="wsl2_performance",
                severity="medium",
                services=["sonarr"],
                description="WSL2 performance issue",
            ),
        ]
        yaml_out, changed = _generate_solution_yaml(conflicts, services)
        assert yaml_out is None
        assert changed == []

    def test_path_conflicts_still_produce_yaml(self):
        """Category A (path) conflicts must still produce solution YAML."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/mnt/tv:/tv"]),
            _make_service("qbittorrent", "linuxserver/qbittorrent", "download_client", ["/mnt/downloads:/downloads"]),
        ]
        conflicts = [
            Conflict(
                conflict_type="no_shared_mount",
                severity="critical",
                services=["sonarr", "qbittorrent"],
                description="No shared mount",
            ),
        ]
        yaml_out, changed = _generate_solution_yaml(conflicts, services)
        assert yaml_out is not None
        assert "services:" in yaml_out
        assert len(changed) > 0

    def test_mixed_conflicts_only_uses_path(self):
        """Mixed conflicts: only Category A should drive solution YAML generation."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/mnt/tv:/tv"]),
            _make_service("qbittorrent", "linuxserver/qbittorrent", "download_client", ["/mnt/downloads:/downloads"]),
        ]
        conflicts = [
            Conflict(
                conflict_type="no_shared_mount",
                severity="critical",
                services=["sonarr", "qbittorrent"],
                description="No shared mount",
            ),
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "qbittorrent"],
                description="PUID mismatch",
            ),
        ]
        yaml_out, changed = _generate_solution_yaml(conflicts, services)
        assert yaml_out is not None
        assert "services:" in yaml_out

    def test_empty_conflicts_no_yaml(self):
        """Empty conflict list should produce no YAML (existing behavior)."""
        services = [
            _make_service("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"]),
        ]
        yaml_out, changed = _generate_solution_yaml([], services)
        assert yaml_out is None
        assert changed == []

    def test_full_stack_permission_only_no_solution_yaml(self):
        """Full analyze_stack with permission-only issues returns no solution_yaml."""
        stack = make_stack({
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "environment": {"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"},
                    "volumes": ["/data:/data"],
                },
                "radarr": {
                    "image": "linuxserver/radarr",
                    "environment": {"PUID": "1001", "PGID": "1000", "TZ": "America/New_York"},
                    "volumes": ["/data:/data"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "environment": {"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"},
                    "volumes": ["/data:/data"],
                },
            },
        })
        result = analyze_stack(
            yaml.safe_load((Path(stack) / "docker-compose.yml").read_text()),
            stack, "docker-compose.yml", "manual"
        )
        # Stack has shared /data mount so no path conflicts, but PUID mismatch exists
        assert result.solution_yaml is None
