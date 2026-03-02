"""
Tests for Pass 4: Platform Recommendations.

Validates WSL2 performance detection, mixed mount type flagging,
Windows path detection, fix generation, and full integration.
"""

import pytest

from backend.analyzer import (
    Conflict,
    ServiceInfo,
    VolumeMount,
    _check_platform,
    _check_wsl2_performance,
    _check_mixed_mount_types,
    _check_windows_paths,
    _collect_participant_mount_types,
    _generate_fixes,
    analyze_stack,
)
from backend.mounts import MountClassification
from backend.resolver import resolve_compose


# ─── Helpers ───

def _make_service(name, role="arr", volumes=None):
    """Create a ServiceInfo with specified volumes."""
    return ServiceInfo(
        name=name,
        image=f"lscr.io/linuxserver/{name}:latest",
        role=role,
        environment={"PUID": "1000", "PGID": "1000"},
        volumes=volumes or [],
        data_paths=["/data"],
    )


def _make_vol(source, target="/data", is_bind=True):
    """Create a VolumeMount."""
    return VolumeMount(
        raw=f"{source}:{target}",
        source=source,
        target=target,
        is_bind_mount=is_bind,
    )


def _make_mc(path, mount_type, is_remote=False, hardlink_compatible=True):
    """Create a MountClassification."""
    return MountClassification(
        path=path,
        mount_type=mount_type,
        is_remote=is_remote,
        hardlink_compatible=hardlink_compatible,
        detail=f"{mount_type} mount: {path}",
    )


# ─── WSL2 Performance ───

class TestCheckWsl2Performance:
    def test_wsl2_paths_detected(self):
        """WSL2 /mnt/c/ paths should trigger a performance warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/c/media/data")]),
            _make_service("qbittorrent", role="download_client",
                         volumes=[_make_vol("/mnt/c/media/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/c/media/data", "wsl2")],
            "qbittorrent": [_make_mc("/mnt/c/media/data", "wsl2")],
        }

        conflicts = _check_wsl2_performance(svcs, svc_mounts)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "wsl2_performance"
        assert conflicts[0].severity == "medium"
        assert "sonarr" in conflicts[0].services
        assert "qbittorrent" in conflicts[0].services

    def test_no_wsl2_paths_clean(self):
        """Native Linux paths should not trigger WSL2 warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/nas/data", "local")],
        }

        conflicts = _check_wsl2_performance(svcs, svc_mounts)
        assert len(conflicts) == 0

    def test_wsl2_config_mount_ignored(self):
        """Config mounts (/config) should not be classified as data — they're
        filtered out by _collect_participant_mount_types, so they won't appear
        in svc_mounts at all. Testing that empty mounts = no conflict."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/c/config", "/config")]),
        ]
        svc_mounts = {
            "sonarr": [],  # Config mounts filtered upstream
        }

        conflicts = _check_wsl2_performance(svcs, svc_mounts)
        assert len(conflicts) == 0


# ─── Mixed Mount Types ───

class TestCheckMixedMountTypes:
    def test_mixed_local_and_nfs(self):
        """Local + NFS across participants should trigger mixed warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
            _make_service("qbittorrent", role="download_client",
                         volumes=[_make_vol("192.168.1.10:/exports/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/nas/data", "local")],
            "qbittorrent": [_make_mc("192.168.1.10:/exports/data", "nfs", is_remote=True)],
        }

        conflicts = _check_mixed_mount_types(svcs, svc_mounts)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "mixed_mount_types"
        assert conflicts[0].severity == "medium"
        assert "sonarr" in conflicts[0].services
        assert "qbittorrent" in conflicts[0].services

    def test_all_local_clean(self):
        """All-local mounts should not trigger mixed warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
            _make_service("qbittorrent", role="download_client",
                         volumes=[_make_vol("/mnt/nas/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/nas/data", "local")],
            "qbittorrent": [_make_mc("/mnt/nas/data", "local")],
        }

        conflicts = _check_mixed_mount_types(svcs, svc_mounts)
        assert len(conflicts) == 0

    def test_all_remote_not_flagged_as_mixed(self):
        """All-NFS mounts are not a mixed issue (handled by remote_filesystem)."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("server:/data")]),
            _make_service("qbittorrent", role="download_client",
                         volumes=[_make_vol("server:/downloads")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("server:/data", "nfs", is_remote=True)],
            "qbittorrent": [_make_mc("server:/downloads", "nfs", is_remote=True)],
        }

        conflicts = _check_mixed_mount_types(svcs, svc_mounts)
        assert len(conflicts) == 0

    def test_single_service_no_mixed(self):
        """A single participant can't have a mixed mount type conflict."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/nas/data", "local")],
        }

        conflicts = _check_mixed_mount_types(svcs, svc_mounts)
        assert len(conflicts) == 0


# ─── Windows Paths ───

class TestCheckWindowsPaths:
    def test_windows_drive_letters_detected(self):
        """Windows C:\\ paths should trigger informational warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("C:\\Users\\media\\data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("C:\\Users\\media\\data", "windows")],
        }

        conflicts = _check_windows_paths(svcs, svc_mounts)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "windows_path_in_compose"
        assert conflicts[0].severity == "low"

    def test_unix_paths_clean(self):
        """Standard Unix paths should not trigger Windows warning."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
        ]
        svc_mounts = {
            "sonarr": [_make_mc("/mnt/nas/data", "local")],
        }

        conflicts = _check_windows_paths(svcs, svc_mounts)
        assert len(conflicts) == 0


# ─── Orchestrator ───

class TestCheckPlatform:
    def test_no_participants_returns_empty(self):
        """Only utility services — no platform checks needed."""
        svcs = [
            _make_service("watchtower", role="other",
                         volumes=[_make_vol("/var/run/docker.sock", "/var/run/docker.sock")]),
        ]
        classifications = [_make_mc("/var/run/docker.sock", "local")]

        conflicts = _check_platform(svcs, classifications)
        assert len(conflicts) == 0

    def test_multiple_platform_issues(self):
        """WSL2 + Windows can fire together if a service has both types."""
        svcs = [
            _make_service("sonarr", volumes=[
                _make_vol("/mnt/c/media/data"),
            ]),
            _make_service("qbittorrent", role="download_client", volumes=[
                _make_vol("D:\\downloads"),
            ]),
        ]
        classifications = [
            _make_mc("/mnt/c/media/data", "wsl2"),
            _make_mc("D:\\downloads", "windows"),
        ]

        conflicts = _check_platform(svcs, classifications)
        types = {c.conflict_type for c in conflicts}
        assert "wsl2_performance" in types
        assert "windows_path_in_compose" in types

    def test_healthy_local_stack_clean(self):
        """Standard local paths with shared mount — no platform issues."""
        svcs = [
            _make_service("sonarr", volumes=[_make_vol("/mnt/nas/data")]),
            _make_service("qbittorrent", role="download_client",
                         volumes=[_make_vol("/mnt/nas/data")]),
        ]
        classifications = [_make_mc("/mnt/nas/data", "local")]

        conflicts = _check_platform(svcs, classifications)
        assert len(conflicts) == 0


# ─── Collect Participant Mount Types ───

class TestCollectParticipantMountTypes:
    def test_maps_data_volumes_only(self):
        """Config mounts should be excluded from the mapping."""
        svcs = [
            _make_service("sonarr", volumes=[
                _make_vol("./config/sonarr", "/config"),
                _make_vol("/mnt/nas/data", "/data"),
            ]),
        ]
        classifications = [
            _make_mc("/mnt/nas/data", "local"),
            # ./config/sonarr wouldn't be classified as it's a config mount
        ]

        result = _collect_participant_mount_types(svcs, classifications)
        assert len(result["sonarr"]) == 1
        assert result["sonarr"][0].path == "/mnt/nas/data"

    def test_non_bind_mounts_excluded(self):
        """Named volumes (non-bind) should be excluded."""
        svcs = [
            _make_service("sonarr", volumes=[
                _make_vol("sonarr_data", "/data", is_bind=False),
            ]),
        ]
        classifications = [_make_mc("sonarr_data", "named_volume")]

        result = _collect_participant_mount_types(svcs, classifications)
        assert len(result["sonarr"]) == 0


# ─── Fix Generation ───

class TestPlatformFixes:
    def test_fix_wsl2_performance(self):
        """WSL2 fix should recommend native Linux paths."""
        conflict = Conflict(
            conflict_type="wsl2_performance",
            severity="medium",
            services=["sonarr", "qbittorrent"],
            description="test",
        )
        _generate_fixes([conflict], [
            _make_service("sonarr"),
            _make_service("qbittorrent", role="download_client"),
        ])
        assert conflict.fix is not None
        assert "native Linux" in conflict.fix
        assert "trash-guides" in conflict.fix.lower()

    def test_fix_mixed_mount_types(self):
        """Mixed mount fix should recommend standardization."""
        svcs = [
            _make_service("sonarr"),
            _make_service("qbittorrent", role="download_client"),
        ]
        conflict = Conflict(
            conflict_type="mixed_mount_types",
            severity="medium",
            services=["sonarr", "qbittorrent"],
            description="test",
        )
        _generate_fixes([conflict], svcs)
        assert conflict.fix is not None
        assert "Standardize" in conflict.fix
        assert "sonarr" in conflict.fix
        assert "qbittorrent" in conflict.fix

    def test_fix_windows_paths(self):
        """Windows path fix should recommend forward slashes."""
        conflict = Conflict(
            conflict_type="windows_path_in_compose",
            severity="low",
            services=["sonarr"],
            description="test",
        )
        _generate_fixes([conflict], [_make_service("sonarr")])
        assert conflict.fix is not None
        assert "forward slashes" in conflict.fix

    def test_fix_remote_filesystem_fallback(self):
        """Remote FS fix should work as fallback when inline fix is cleared."""
        conflict = Conflict(
            conflict_type="remote_filesystem",
            severity="high",
            services=["sonarr"],
            description="test",
            fix=None,  # Simulate cleared inline fix
        )
        _generate_fixes([conflict], [_make_service("sonarr")])
        assert conflict.fix is not None
        assert "local storage" in conflict.fix

    def test_remote_filesystem_preserves_inline_fix(self):
        """Remote FS should NOT overwrite an existing inline fix."""
        conflict = Conflict(
            conflict_type="remote_filesystem",
            severity="high",
            services=["sonarr"],
            description="test",
            fix="Existing inline fix from _add_mount_conflicts",
        )
        _generate_fixes([conflict], [_make_service("sonarr")])
        assert conflict.fix == "Existing inline fix from _add_mount_conflicts"


# ─── Integration ───

class TestPlatformIntegration:
    def test_wsl2_stack_has_platform_conflict(self, make_stack):
        """Full analyze_stack with WSL2 paths should produce wsl2_performance."""
        from tests.conftest import WSL2_MOUNT_YAML
        stack_path = make_stack(WSL2_MOUNT_YAML)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        conflict_types = [c.conflict_type for c in result.conflicts]
        assert "wsl2_performance" in conflict_types

    def test_healthy_stack_shows_platform_passed(self, make_stack):
        """Healthy stack should have 'Platform check passed' in steps."""
        from tests.conftest import HEALTHY_MULTI_YAML
        stack_path = make_stack(HEALTHY_MULTI_YAML)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        step_texts = [s["text"] for s in result.steps]
        assert any("Platform check passed" in t for t in step_texts)

    def test_platform_conflicts_have_fixes(self, make_stack):
        """All platform conflicts should have fix text after analysis."""
        from tests.conftest import WSL2_MOUNT_YAML
        stack_path = make_stack(WSL2_MOUNT_YAML)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        platform_types = {"wsl2_performance", "mixed_mount_types", "windows_path_in_compose"}
        for conflict in result.conflicts:
            if conflict.conflict_type in platform_types:
                assert conflict.fix is not None, f"{conflict.conflict_type} has no fix text"
