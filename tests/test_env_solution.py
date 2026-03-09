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
    _generate_env_solution,
    _find_majority_env,
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


# ─── Helpers for env solution tests ───

def _make_service_with_env(name, image, role, volumes_raw, environment):
    """Build a ServiceInfo with parsed volumes and environment."""
    svc = _make_service(name, image, role, volumes_raw)
    svc.environment = dict(environment)
    return svc


def analyze_and_get_result(compose_data):
    """Create a temp stack and run analyze_stack, returning the AnalysisResult."""
    stack = make_stack(compose_data)
    return analyze_stack(
        yaml.safe_load((Path(stack) / "docker-compose.yml").read_text()),
        stack, "docker-compose.yml", "manual"
    )


# ═══════════════════════════════════════════
# Environment Solution Generator
# ═══════════════════════════════════════════

class TestFindMajorityEnv:
    """Tests for the _find_majority_env helper."""

    def test_majority_puid(self):
        """Most common PUID value wins."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"PUID": "1000"}),
            _make_service_with_env("qbit", "linuxserver/qbittorrent", "download_client", ["/data:/data"],
                                   {"PUID": "1001"}),
        ]
        assert _find_majority_env(services, "PUID", "1000") == "1000"

    def test_missing_key_returns_default(self):
        """When no service has the key, return default."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"], {}),
        ]
        assert _find_majority_env(services, "PUID", "1000") == "1000"

    def test_ignores_non_media_services(self):
        """Non-media services (role='other') should be ignored."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000"}),
            _make_service_with_env("nginx", "nginx", "other", [],
                                   {"PUID": "99"}),
        ]
        assert _find_majority_env(services, "PUID", "1000") == "1000"


class TestEnvSolution:
    """Tests for _generate_env_solution()."""

    def test_puid_mismatch_generates_env_yaml(self):
        """PUID mismatch -> env_solution_yaml with corrected PUID/PGID."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"PUID": "1001", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service_with_env("qbit", "linuxserver/qbittorrent", "download_client", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "radarr", "qbit"],
                description="PUID mismatch",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is not None
        assert "PUID=" in yaml_out
        assert "services:" in yaml_out

    def test_puid_mismatch_no_volume_yaml(self):
        """PUID mismatch should NOT produce volume solution_yaml."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"PUID": "1001", "PGID": "1000"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "radarr"],
                description="PUID mismatch",
            ),
        ]
        vol_yaml, _ = _generate_solution_yaml(conflicts, services)
        assert vol_yaml is None

    def test_umask_mismatch_generates_env_yaml(self):
        """UMASK mismatch -> env_solution_yaml with UMASK=002."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000", "UMASK": "022"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000", "UMASK": "077"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="umask_inconsistent",
                severity="low",
                services=["sonarr", "radarr"],
                description="UMASK inconsistent",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is not None
        assert "UMASK=002" in yaml_out

    def test_tz_mismatch_generates_env_yaml(self):
        """TZ mismatch -> env_solution_yaml with majority TZ."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"TZ": "America/New_York"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"TZ": "America/New_York"}),
            _make_service_with_env("qbit", "linuxserver/qbittorrent", "download_client", ["/data:/data"],
                                   {"TZ": "Europe/London"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="tz_mismatch",
                severity="medium",
                services=["sonarr", "radarr", "qbit"],
                description="TZ mismatch",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is not None
        assert "America/New_York" in yaml_out

    def test_path_only_no_env_yaml(self):
        """Path-only conflict -> no env_solution_yaml."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/mnt/tv:/tv"],
                                   {"PUID": "1000"}),
            _make_service_with_env("qbit", "linuxserver/qbittorrent", "download_client", ["/mnt/dl:/downloads"],
                                   {"PUID": "1000"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="no_shared_mount",
                severity="critical",
                services=["sonarr", "qbit"],
                description="No shared mount",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is None
        assert changed == []

    def test_mixed_generates_both(self):
        """Mixed A+B -> both solution_yaml AND env_solution_yaml."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/mnt/tv:/tv"],
                                   {"PUID": "1000", "PGID": "1000"}),
            _make_service_with_env("qbit", "linuxserver/qbittorrent", "download_client", ["/mnt/dl:/downloads"],
                                   {"PUID": "1001", "PGID": "1000"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="no_shared_mount",
                severity="critical",
                services=["sonarr", "qbit"],
                description="No shared mount",
            ),
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "qbit"],
                description="PUID mismatch",
            ),
        ]
        vol_yaml, _ = _generate_solution_yaml(conflicts, services)
        env_yaml, _ = _generate_env_solution(conflicts, services)
        assert vol_yaml is not None
        assert env_yaml is not None

    def test_infra_only_no_env_yaml(self):
        """Cat C only -> no env_solution_yaml."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="wsl2_performance",
                severity="medium",
                services=["sonarr"],
                description="WSL2 issue",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is None
        assert changed == []

    def test_env_solution_has_changed_lines(self):
        """Changed lines should be tracked for highlighting."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000", "PGID": "1000"}),
            _make_service_with_env("radarr", "linuxserver/radarr", "arr", ["/data:/data"],
                                   {"PUID": "1001", "PGID": "1000"}),
        ]
        conflicts = [
            Conflict(
                conflict_type="puid_pgid_mismatch",
                severity="high",
                services=["sonarr", "radarr"],
                description="PUID mismatch",
            ),
        ]
        yaml_out, changed = _generate_env_solution(conflicts, services)
        assert yaml_out is not None
        assert len(changed) > 0
        # All changed lines should be positive integers
        assert all(isinstance(n, int) and n > 0 for n in changed)

    def test_empty_conflicts_no_env_yaml(self):
        """Empty conflict list -> no env YAML."""
        services = [
            _make_service_with_env("sonarr", "linuxserver/sonarr", "arr", ["/data:/data"],
                                   {"PUID": "1000"}),
        ]
        yaml_out, changed = _generate_env_solution([], services)
        assert yaml_out is None
        assert changed == []

    def test_full_stack_puid_mismatch_has_env_yaml(self):
        """Full analyze_stack with PUID mismatch returns env_solution_yaml."""
        result = analyze_and_get_result({
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
        # Should have env solution but NOT volume solution
        assert result.solution_yaml is None
        assert result.env_solution_yaml is not None
        assert "PUID=" in result.env_solution_yaml
