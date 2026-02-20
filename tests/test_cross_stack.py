"""
Tests for MapArr — Cross-Stack Analysis.

Covers:
  - _find_compose_file() — locating compose files in directories
  - _parse_sibling_services() — lightweight YAML parsing for media services
  - _check_shared_root() — mount root comparison algorithm
  - check_cross_stack() — full sibling scanning
  - SiblingService / CrossStackResult dataclasses
  - Edge cases: no compose file, empty YAML, oversized files
"""

import os
import textwrap

import pytest

from conftest import (
    SONARR_YAML, QBITTORRENT_YAML, PLEX_YAML,
    UTILITY_YAML, SONARR_CONFLICT_YAML,
)


# ═══════════════════════════════════════════
# Unit Tests: _find_compose_file()
# ═══════════════════════════════════════════

class TestFindComposeFile:
    """Locate compose files in directories."""

    def test_docker_compose_yml(self, tmp_path):
        from backend.cross_stack import _find_compose_file

        (tmp_path / "docker-compose.yml").write_text("services: {}")
        assert _find_compose_file(str(tmp_path)) is not None

    def test_compose_yml(self, tmp_path):
        from backend.cross_stack import _find_compose_file

        (tmp_path / "compose.yml").write_text("services: {}")
        assert _find_compose_file(str(tmp_path)) is not None

    def test_compose_yaml(self, tmp_path):
        from backend.cross_stack import _find_compose_file

        (tmp_path / "compose.yaml").write_text("services: {}")
        assert _find_compose_file(str(tmp_path)) is not None

    def test_no_compose_file(self, tmp_path):
        from backend.cross_stack import _find_compose_file

        assert _find_compose_file(str(tmp_path)) is None

    def test_priority_docker_compose_first(self, tmp_path):
        """docker-compose.yml should be found before compose.yml."""
        from backend.cross_stack import _find_compose_file

        (tmp_path / "docker-compose.yml").write_text("services: {}")
        (tmp_path / "compose.yml").write_text("services: {}")
        result = _find_compose_file(str(tmp_path))
        assert "docker-compose.yml" in result


# ═══════════════════════════════════════════
# Unit Tests: _parse_sibling_services()
# ═══════════════════════════════════════════

class TestParseSiblingServices:
    """Lightweight YAML parsing for media services."""

    def test_parse_sonarr(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(SONARR_YAML)
        result = _parse_sibling_services(str(compose))

        assert "sonarr" in result
        assert result["sonarr"]["role"] == "arr"
        assert len(result["sonarr"]["host_sources"]) > 0

    def test_parse_download_client(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(QBITTORRENT_YAML)
        result = _parse_sibling_services(str(compose))

        assert "qbittorrent" in result
        assert result["qbittorrent"]["role"] == "download_client"

    def test_parse_media_server(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(PLEX_YAML)
        result = _parse_sibling_services(str(compose))

        assert "plex" in result
        assert result["plex"]["role"] == "media_server"

    def test_skip_utility_services(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(UTILITY_YAML)
        result = _parse_sibling_services(str(compose))

        assert len(result) == 0  # watchtower is not a media service

    def test_empty_yaml(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("")
        result = _parse_sibling_services(str(compose))

        assert len(result) == 0

    def test_no_services_key(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("version: '3'\nnetworks:\n  default:\n")
        result = _parse_sibling_services(str(compose))

        assert len(result) == 0

    def test_invalid_yaml(self, tmp_path):
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("{{{{invalid yaml!!!!}")
        result = _parse_sibling_services(str(compose))

        assert len(result) == 0

    def test_config_mounts_filtered(self, tmp_path):
        """Config mounts should not appear in host_sources."""
        from backend.cross_stack import _parse_sibling_services

        compose = tmp_path / "docker-compose.yml"
        compose.write_text(SONARR_YAML)
        result = _parse_sibling_services(str(compose))

        sources = result["sonarr"]["host_sources"]
        # ./config should be filtered, /mnt/nas/data should remain
        for s in sources:
            assert "/config" not in s


# ═══════════════════════════════════════════
# Unit Tests: _check_shared_root()
# ═══════════════════════════════════════════

class TestCheckSharedRoot:
    """Mount root comparison algorithm."""

    def test_exact_match(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data"}
        sibling = SiblingService("", "", "", "", {"/mnt/nas/data"}, "")
        shared, root = _check_shared_root(current, [sibling])

        assert shared is True
        assert root == "/mnt/nas/data"

    def test_parent_child_match(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data/tv"}
        sibling = SiblingService("", "", "", "", {"/mnt/nas/data"}, "")
        shared, root = _check_shared_root(current, [sibling])

        assert shared is True

    def test_common_prefix_match(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data/tv"}
        sibling = SiblingService("", "", "", "", {"/mnt/nas/data/movies"}, "")
        shared, root = _check_shared_root(current, [sibling])

        assert shared is True
        assert "/mnt/nas" in root

    def test_no_shared_root(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/host/tv"}
        sibling = SiblingService("", "", "", "", {"/mnt/nas/data"}, "")
        shared, root = _check_shared_root(current, [sibling])

        assert shared is False

    def test_empty_current_sources(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        sibling = SiblingService("", "", "", "", {"/mnt/nas/data"}, "")
        shared, root = _check_shared_root(set(), [sibling])

        assert shared is False

    def test_no_sibling_sources(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data"}
        sibling = SiblingService("", "", "", "", set(), "")
        shared, root = _check_shared_root(current, [sibling])

        assert shared is False

    def test_multiple_siblings_all_shared(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data"}
        siblings = [
            SiblingService("", "", "", "", {"/mnt/nas/data"}, ""),
            SiblingService("", "", "", "", {"/mnt/nas/data"}, ""),
            SiblingService("", "", "", "", {"/mnt/nas/data"}, ""),
        ]
        shared, root = _check_shared_root(current, siblings)

        assert shared is True

    def test_one_divergent_sibling(self):
        from backend.cross_stack import _check_shared_root, SiblingService

        current = {"/mnt/nas/data"}
        siblings = [
            SiblingService("", "", "", "", {"/mnt/nas/data"}, ""),
            SiblingService("", "", "", "", {"/different/path"}, ""),
        ]
        shared, root = _check_shared_root(current, siblings)

        assert shared is False


# ═══════════════════════════════════════════
# Unit Tests: check_cross_stack()
# ═══════════════════════════════════════════

class TestCheckCrossStack:
    """Full sibling scanning."""

    def test_finds_complementary_services(self, make_pipeline_dir):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        # Sonarr has arr role but no download client
        sonarr_svc = ServiceInfo(name="sonarr", role="arr")
        result = check_cross_stack(
            os.path.join(root, "sonarr"),
            root,
            [sonarr_svc],
            {"/mnt/nas/data"},
        )

        assert result is not None
        assert len(result.siblings_found) >= 1
        assert result.siblings_found[0].role == "download_client"
        assert "download_client" in result.missing_roles_filled

    def test_shared_mount_detected(self, make_pipeline_dir):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        sonarr_svc = ServiceInfo(name="sonarr", role="arr")
        result = check_cross_stack(
            os.path.join(root, "sonarr"),
            root,
            [sonarr_svc],
            {"/mnt/nas/data"},
        )

        assert result is not None
        assert result.shared_mount is True

    def test_conflict_detected(self, make_pipeline_dir):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({
            "sonarr": SONARR_CONFLICT_YAML,  # /different/path
            "qbittorrent": QBITTORRENT_YAML,  # /mnt/nas/data
        })

        sonarr_svc = ServiceInfo(name="sonarr", role="arr")
        result = check_cross_stack(
            os.path.join(root, "sonarr"),
            root,
            [sonarr_svc],
            {"/different/path"},
        )

        assert result is not None
        assert result.shared_mount is False
        assert len(result.conflicts) >= 1

    def test_complete_stack_returns_none(self, make_pipeline_dir):
        """A complete stack (arr + download client + media server) doesn't need cross-stack scan."""
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({"sonarr": SONARR_YAML})

        services = [
            ServiceInfo(name="sonarr", role="arr"),
            ServiceInfo(name="qbittorrent", role="download_client"),
            ServiceInfo(name="plex", role="media_server"),
        ]
        result = check_cross_stack(os.path.join(root, "sonarr"), root, services)

        assert result is None  # No scan needed — all roles present

    def test_invalid_scan_dir(self):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        result = check_cross_stack("/nonexistent", "/nonexistent", [ServiceInfo(name="sonarr", role="arr")])
        assert result is None

    def test_no_siblings_found(self, make_pipeline_dir):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "watchtower": UTILITY_YAML,
        })

        sonarr_svc = ServiceInfo(name="sonarr", role="arr")
        result = check_cross_stack(
            os.path.join(root, "sonarr"),
            root,
            [sonarr_svc],
        )

        assert result is not None
        assert len(result.siblings_found) == 0
        assert result.sibling_count_scanned >= 1

    def test_cross_stack_result_to_dict(self, make_pipeline_dir):
        from backend.cross_stack import check_cross_stack
        from backend.analyzer import ServiceInfo

        root = make_pipeline_dir({
            "sonarr": SONARR_YAML,
            "qbittorrent": QBITTORRENT_YAML,
        })

        sonarr_svc = ServiceInfo(name="sonarr", role="arr")
        result = check_cross_stack(
            os.path.join(root, "sonarr"),
            root,
            [sonarr_svc],
            {"/mnt/nas/data"},
        )

        d = result.to_dict()
        assert "siblings" in d
        assert "shared_mount" in d
        assert "mount_root" in d
        assert "conflicts" in d
        assert "summary" in d
