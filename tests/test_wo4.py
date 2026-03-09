"""
Tests for MapArr — Mount Intelligence.

Tests cover:
  - Path classification (NFS, CIFS, UNC, Windows, WSL2, local, named volume, relative)
  - Hardlink compatibility checking
  - Analyzer integration (mount_warnings, mount_info in results)
  - Remote filesystem conflict generation
  - API response includes mount data
"""

import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.mounts import (
    MountClassification,
    classify_path,
    classify_volume_sources,
    check_hardlink_compatibility,
)
from backend.analyzer import analyze_stack

client = TestClient(app)


# ─── Helper ───

def make_stack(compose_data: dict) -> str:
    tmpdir = tempfile.mkdtemp(prefix="maparr_test_")
    (Path(tmpdir) / "docker-compose.yml").write_text(
        yaml.dump(compose_data), encoding="utf-8"
    )
    return tmpdir


# ═══════════════════════════════════════════
# Path Classification — NFS
# ═══════════════════════════════════════════

class TestNFSClassification:

    def test_nfs_url(self):
        mc = classify_path("nfs://192.168.1.10/export/media")
        assert mc.mount_type == "nfs"
        assert mc.is_remote is True
        assert mc.hardlink_compatible is False
        assert "192.168.1.10" in mc.detail

    def test_nfs_colon_syntax(self):
        mc = classify_path("nas.local:/export/media")
        assert mc.mount_type == "nfs"
        assert mc.is_remote is True
        assert mc.hardlink_compatible is False
        assert "nas.local" in mc.detail

    def test_nfs_ip_colon(self):
        mc = classify_path("192.168.1.10:/data/media")
        assert mc.mount_type == "nfs"
        assert mc.is_remote is True
        assert "192.168.1.10" in mc.detail

    def test_nfs_url_case_insensitive(self):
        mc = classify_path("NFS://mynas/share")
        assert mc.mount_type == "nfs"
        assert mc.is_remote is True

    def test_single_letter_colon_not_nfs(self):
        """C:/path should NOT be classified as NFS."""
        mc = classify_path("C:/Users/data")
        assert mc.mount_type != "nfs"


# ═══════════════════════════════════════════
# Path Classification — CIFS/UNC
# ═══════════════════════════════════════════

class TestCIFSClassification:

    def test_unc_backslash(self):
        mc = classify_path("\\\\server\\share\\media")
        assert mc.mount_type == "cifs"
        assert mc.is_remote is True
        assert mc.hardlink_compatible is False
        assert "server" in mc.detail

    def test_unc_forward_slash(self):
        mc = classify_path("//server/share/media")
        assert mc.mount_type == "cifs"
        assert mc.is_remote is True
        assert "server" in mc.detail

    def test_unc_has_warning(self):
        mc = classify_path("//nas/media")
        assert mc.warning is not None
        assert "CIFS" in mc.warning or "SMB" in mc.warning


# ═══════════════════════════════════════════
# Path Classification — Windows
# ═══════════════════════════════════════════

class TestWindowsClassification:

    def test_windows_backslash(self):
        mc = classify_path("C:\\Users\\data\\media")
        assert mc.mount_type == "windows"
        assert mc.is_remote is False
        assert mc.hardlink_compatible is True

    def test_windows_forward_slash(self):
        mc = classify_path("D:/Docker/data")
        assert mc.mount_type == "windows"
        assert mc.is_remote is False

    def test_windows_drive_letter_in_detail(self):
        mc = classify_path("E:/media")
        assert "E:" in mc.detail


# ═══════════════════════════════════════════
# Path Classification — WSL2
# ═══════════════════════════════════════════

class TestWSL2Classification:

    def test_wsl2_mnt_c(self):
        mc = classify_path("/mnt/c/Users/data")
        assert mc.mount_type == "wsl2"
        assert mc.is_remote is False
        assert mc.hardlink_compatible is True
        assert "C:" in mc.detail

    def test_wsl2_mnt_d(self):
        mc = classify_path("/mnt/d/Docker/data")
        assert mc.mount_type == "wsl2"
        assert "D:" in mc.detail

    def test_wsl2_performance_warning(self):
        mc = classify_path("/mnt/c/data")
        assert mc.warning is not None
        assert "performance" in mc.warning.lower() or "WSL2" in mc.warning


# ═══════════════════════════════════════════
# Path Classification — Named Volumes
# ═══════════════════════════════════════════

class TestNamedVolumeClassification:

    def test_named_volume(self):
        mc = classify_path("mydata")
        assert mc.mount_type == "named_volume"
        assert mc.is_remote is False
        assert mc.hardlink_compatible is False

    def test_named_volume_with_underscore(self):
        mc = classify_path("pg_data")
        assert mc.mount_type == "named_volume"

    def test_named_volume_with_dash(self):
        mc = classify_path("my-volume")
        assert mc.mount_type == "named_volume"

    def test_named_volume_with_dot(self):
        mc = classify_path("app.data")
        assert mc.mount_type == "named_volume"

    def test_path_not_named_volume(self):
        mc = classify_path("/data")
        assert mc.mount_type != "named_volume"

    def test_relative_not_named_volume(self):
        mc = classify_path("./config")
        assert mc.mount_type != "named_volume"

    def test_starts_with_number_not_named(self):
        mc = classify_path("123data")
        assert mc.mount_type != "named_volume"


# ═══════════════════════════════════════════
# Path Classification — Local & Relative
# ═══════════════════════════════════════════

class TestLocalClassification:

    def test_absolute_linux_path(self):
        mc = classify_path("/data/media")
        assert mc.mount_type == "local"
        assert mc.is_remote is False
        assert mc.hardlink_compatible is True

    def test_relative_dot_slash(self):
        mc = classify_path("./config/sonarr")
        assert mc.mount_type == "relative"
        assert mc.is_remote is False
        assert mc.hardlink_compatible is True

    def test_relative_dot_dot(self):
        mc = classify_path("../shared/data")
        assert mc.mount_type == "relative"

    def test_empty_path(self):
        mc = classify_path("")
        assert mc.mount_type == "unknown"
        assert mc.hardlink_compatible is True


# ═══════════════════════════════════════════
# Batch Classification
# ═══════════════════════════════════════════

class TestBatchClassification:

    def test_classify_multiple(self):
        results = classify_volume_sources([
            "/data/media",
            "//nas/share",
            "myvolume",
        ])
        assert len(results) == 3
        assert results[0].mount_type == "local"
        assert results[1].mount_type == "cifs"
        assert results[2].mount_type == "named_volume"


# ═══════════════════════════════════════════
# Hardlink Compatibility Checking
# ═══════════════════════════════════════════

class TestHardlinkCompatibility:

    def test_all_local_no_warnings(self):
        mcs = [
            classify_path("/data/media"),
            classify_path("/data/torrents"),
        ]
        warnings = check_hardlink_compatibility(mcs)
        assert len(warnings) == 0

    def test_remote_generates_warning(self):
        mcs = [
            classify_path("/data/media"),
            classify_path("//nas/share"),
        ]
        warnings = check_hardlink_compatibility(mcs)
        assert len(warnings) > 0

    def test_cifs_specific_warning(self):
        mcs = [classify_path("//nas/media")]
        warnings = check_hardlink_compatibility(mcs)
        assert any("CIFS" in w or "SMB" in w for w in warnings)

    def test_nfs_specific_warning(self):
        mcs = [classify_path("nas.local:/export/data")]
        warnings = check_hardlink_compatibility(mcs)
        assert any("NFS" in w for w in warnings)

    def test_mixed_named_and_bind_warning(self):
        mcs = [
            classify_path("/data/media"),
            classify_path("myvolume"),
        ]
        warnings = check_hardlink_compatibility(mcs)
        assert any("named volume" in w.lower() for w in warnings)

    def test_all_remote_still_warns(self):
        mcs = [
            classify_path("//nas/share1"),
            classify_path("//nas/share2"),
        ]
        warnings = check_hardlink_compatibility(mcs)
        assert len(warnings) > 0


# ═══════════════════════════════════════════
# Analyzer Integration
# ═══════════════════════════════════════════

class TestAnalyzerIntegration:

    def test_local_stack_no_mount_warnings(self):
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
        assert result.mount_warnings == []
        assert len(result.mount_info) > 0

    def test_remote_mount_generates_warnings(self):
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["//nas/media:/data", "./config:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["//nas/downloads:/downloads", "./config:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert len(result.mount_warnings) > 0

    def test_remote_mount_adds_conflict(self):
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["//nas/media:/data/tv", "./config:/config"],
                },
                "qbittorrent": {
                    "image": "linuxserver/qbittorrent",
                    "volumes": ["//nas/downloads:/downloads", "./config:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        remote_conflicts = [c for c in result.conflicts if c.conflict_type == "remote_filesystem"]
        assert len(remote_conflicts) > 0
        assert remote_conflicts[0].severity == "high"

    def test_mount_info_in_result(self):
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/mnt/c/data:/data", "./config:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        assert any(mi["mount_type"] == "wsl2" for mi in result.mount_info)

    def test_config_mounts_not_classified(self):
        """Config mounts should NOT appear in mount_info."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "linuxserver/sonarr",
                    "volumes": ["/data:/data", "./config:/config"],
                },
            },
            "_resolution": "manual",
            "_compose_file": "docker-compose.yml",
            "_warnings": [],
        }
        result = analyze_stack(compose, "/tmp/test", "docker-compose.yml", "manual")
        # ./config is a config mount and should be filtered out
        mount_paths = [mi["path"] for mi in result.mount_info]
        assert "./config" not in mount_paths


# ═══════════════════════════════════════════
# API Response Shape
# ═══════════════════════════════════════════

class TestAPIResponse:

    def test_response_includes_mount_fields(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["/data:/data"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["/data:/data"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        assert "mount_warnings" in data
        assert "mount_info" in data
        assert isinstance(data["mount_warnings"], list)
        assert isinstance(data["mount_info"], list)

    def test_remote_mount_in_api_response(self):
        stack = make_stack({
            "services": {
                "sonarr": {"image": "linuxserver/sonarr", "volumes": ["//nas/media:/data"]},
                "qbittorrent": {"image": "linuxserver/qbittorrent", "volumes": ["//nas/dl:/downloads"]},
            }
        })
        resp = client.post("/api/analyze", json={"stack_path": stack})
        data = resp.json()
        assert len(data["mount_warnings"]) > 0
        # Should have mount_info with CIFS entries
        cifs_mounts = [m for m in data["mount_info"] if m["mount_type"] == "cifs"]
        assert len(cifs_mounts) > 0


# ═══════════════════════════════════════════
# Frontend — Mount Warning Section
# ═══════════════════════════════════════════

class TestFrontendMountWarnings:

    def test_index_has_mount_warning_section(self):
        resp = client.get("/")
        assert resp.status_code == 200
        # Mount warnings are now rendered inline within the problem section
        # rather than in a separate step-mount-warnings section
        assert "step-problem" in resp.text

    def test_app_js_has_mount_warning_function(self):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        # Renamed from showMountWarnings to renderMountWarningsInto
        assert "renderMountWarningsInto" in resp.text


# ═══════════════════════════════════════════
# MountClassification.to_dict()
# ═══════════════════════════════════════════

class TestMountClassificationDict:

    def test_to_dict_fields(self):
        mc = classify_path("/data/media")
        d = mc.to_dict()
        assert "path" in d
        assert "mount_type" in d
        assert "is_remote" in d
        assert "hardlink_compatible" in d
        assert "detail" in d
        assert "warning" in d

    def test_to_dict_values(self):
        mc = classify_path("//nas/share")
        d = mc.to_dict()
        assert d["path"] == "//nas/share"
        assert d["mount_type"] == "cifs"
        assert d["is_remote"] is True
        assert d["hardlink_compatible"] is False


# ═══════════════════════════════════════════
# WSL2 Regex Precision (WO4 Task 2)
# ═══════════════════════════════════════════

class TestWsl2RegexPrecision:
    """Ensure WSL2 detection only matches real Windows drive mounts (c-z with subdirectory)."""

    def test_mnt_n_is_wsl2(self):
        """Single letter n is in c-z range, so /mnt/n/ is a valid WSL2 drive mount.
        NAS paths should use multi-letter names like /mnt/nas/ to avoid ambiguity."""
        mc = classify_path("/mnt/n/export")
        assert mc.mount_type == "wsl2"

    def test_mnt_a_is_not_wsl2(self):
        """Floppy drive letter A should not be classified as WSL2."""
        mc = classify_path("/mnt/a/something")
        assert mc.mount_type != "wsl2"

    def test_mnt_b_is_not_wsl2(self):
        """Floppy drive letter B should not be classified as WSL2."""
        mc = classify_path("/mnt/b/data")
        assert mc.mount_type != "wsl2"

    def test_mnt_c_users_is_wsl2(self):
        mc = classify_path("/mnt/c/Users/media")
        assert mc.mount_type == "wsl2"

    def test_mnt_d_downloads_is_wsl2(self):
        mc = classify_path("/mnt/d/Downloads")
        assert mc.mount_type == "wsl2"

    def test_mnt_z_data_is_wsl2(self):
        mc = classify_path("/mnt/z/data")
        assert mc.mount_type == "wsl2"

    def test_mnt_c_alone_is_not_wsl2(self):
        """Bare /mnt/c without subdirectory is ambiguous — should not match WSL2."""
        mc = classify_path("/mnt/c")
        assert mc.mount_type != "wsl2"

    def test_mnt_nas_is_local(self):
        """Multi-letter names under /mnt/ are not WSL2 drive mounts."""
        mc = classify_path("/mnt/nas/media")
        assert mc.mount_type == "local"
