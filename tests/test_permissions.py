"""
test_permissions.py — Test suite for the Permissions Analysis Pass (Pass 3).

Tests image family detection, permission profile building, all four
permission checks (PUID/PGID mismatch, missing PUID/PGID, root execution,
UMASK consistency), fix generation, and integration with analyze_stack().
"""

import pytest

from backend.analyzer import (
    ImageFamily,
    IMAGE_FAMILIES,
    PermissionProfile,
    ServiceInfo,
    VolumeMount,
    _identify_image_family,
    _build_permission_profile,
    _normalize_umask,
    _check_permissions,
    _check_puid_pgid_mismatch,
    _check_missing_puid_pgid,
    _check_root_execution,
    _check_umask_consistency,
    _check_tz_mismatch,
    _generate_fixes,
)
from backend.resolver import resolve_compose


# ─── Image Family Detection ───


class TestImageFamilyDetection:
    """Tests for _identify_image_family()."""

    def test_linuxserver_lscr(self):
        family = _identify_image_family("lscr.io/linuxserver/sonarr:latest")
        assert family is not None
        assert family.name == "LinuxServer.io"
        assert family.uid_env == "PUID"
        assert family.default_uid == "911"

    def test_linuxserver_dockerhub(self):
        family = _identify_image_family("linuxserver/radarr:develop")
        assert family is not None
        assert family.name == "LinuxServer.io"

    def test_linuxserver_ghcr(self):
        family = _identify_image_family("ghcr.io/linuxserver/sonarr:latest")
        assert family is not None
        assert family.name == "LinuxServer.io"

    def test_hotio(self):
        family = _identify_image_family("hotio/radarr:latest")
        assert family is not None
        assert family.name == "Hotio"
        assert family.uid_env == "PUID"
        assert family.default_uid == "1000"

    def test_hotio_cr(self):
        family = _identify_image_family("cr.hotio.dev/hotio/sonarr:release")
        assert family is not None
        assert family.name == "Hotio"

    def test_jlesage(self):
        family = _identify_image_family("jlesage/jdownloader-2:latest")
        assert family is not None
        assert family.name == "jlesage"
        assert family.uid_env == "USER_ID"
        assert family.gid_env == "GROUP_ID"

    def test_binhex(self):
        family = _identify_image_family("binhex/arch-qbittorrentvpn:latest")
        assert family is not None
        assert family.name == "Binhex"
        assert family.default_uid == "99"

    def test_official_plex(self):
        family = _identify_image_family("plexinc/pms-docker:latest")
        assert family is not None
        assert family.name == "Official Plex"
        assert family.uid_env == "PLEX_UID"

    def test_seerr_overseerr(self):
        family = _identify_image_family("sctx/overseerr:latest")
        assert family is not None
        assert family.name == "Seerr"
        assert family.needs_puid is False

    def test_seerr_jellyseerr(self):
        family = _identify_image_family("fallenbagel/jellyseerr:latest")
        assert family is not None
        assert family.name == "Seerr"

    def test_unknown_image(self):
        assert _identify_image_family("custom/myapp:latest") is None
        assert _identify_image_family("containrrr/watchtower") is None

    def test_case_insensitive(self):
        family = _identify_image_family("LSCR.IO/LINUXSERVER/SONARR:latest")
        assert family is not None
        assert family.name == "LinuxServer.io"

    def test_empty_image(self):
        assert _identify_image_family("") is None


# ─── Permission Profile Building ───


def _make_service(name, image="", role="arr", env=None, compose_user=None):
    """Helper to create ServiceInfo for tests."""
    return ServiceInfo(
        name=name,
        image=image,
        role=role,
        environment=env or {},
        compose_user=compose_user,
        volumes=[
            VolumeMount(raw="./config:/config", source="./config", target="/config"),
            VolumeMount(raw="/data:/data", source="/data", target="/data"),
        ],
        data_paths=["/data"],
    )


class TestPermissionProfile:
    """Tests for _build_permission_profile()."""

    def test_puid_from_env(self):
        svc = _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                            env={"PUID": "1000", "PGID": "1000"})
        profile = _build_permission_profile(svc)
        assert profile.uid == "1000"
        assert profile.gid == "1000"
        assert profile.uid_source == "env_PUID"
        assert profile.gid_source == "env_PGID"
        assert profile.image_family == "LinuxServer.io"
        assert profile.is_root is False

    def test_compose_user_takes_precedence(self):
        """compose user: overrides env vars because Docker enforces it."""
        svc = _make_service("sabnzbd", "lscr.io/linuxserver/sabnzbd",
                            env={"PUID": "999", "PGID": "999"},
                            compose_user="1000:1000")
        profile = _build_permission_profile(svc)
        assert profile.uid == "1000"
        assert profile.gid == "1000"
        assert profile.uid_source == "compose_user"

    def test_compose_user_single_value(self):
        """user: '1000' (no GID) should use UID for both."""
        svc = _make_service("app", "lscr.io/linuxserver/sonarr",
                            compose_user="1000")
        profile = _build_permission_profile(svc)
        assert profile.uid == "1000"
        assert profile.gid == "1000"

    def test_jlesage_user_id(self):
        svc = _make_service("jdownloader2", "jlesage/jdownloader-2",
                            role="download_client",
                            env={"USER_ID": "568", "GROUP_ID": "568"})
        profile = _build_permission_profile(svc)
        assert profile.uid == "568"
        assert profile.gid == "568"
        assert profile.uid_source == "env_USER_ID"
        assert profile.image_family == "jlesage"

    def test_linuxserver_default_uid(self):
        """LSIO image with no PUID/PGID falls back to family default 911."""
        svc = _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                            env={})
        profile = _build_permission_profile(svc)
        assert profile.uid == "911"
        assert profile.gid == "911"
        assert profile.uid_source == "default"
        assert profile.needs_explicit_id is True

    def test_hotio_default_uid(self):
        """Hotio defaults to 1000."""
        svc = _make_service("radarr", "hotio/radarr", env={})
        profile = _build_permission_profile(svc)
        assert profile.uid == "1000"
        assert profile.uid_source == "default"

    def test_root_detection_uid_zero(self):
        svc = _make_service("huntarr", "lscr.io/linuxserver/huntarr",
                            env={"PUID": "0", "PGID": "0"})
        profile = _build_permission_profile(svc)
        assert profile.is_root is True

    def test_root_detection_string(self):
        svc = _make_service("app", "lscr.io/linuxserver/sonarr",
                            compose_user="root")
        profile = _build_permission_profile(svc)
        assert profile.is_root is True

    def test_unknown_image_no_assumptions(self):
        """Unknown image with no env = no assumptions about UID/GID."""
        svc = _make_service("watchtower", "containrrr/watchtower",
                            role="other", env={})
        profile = _build_permission_profile(svc)
        assert profile.uid is None
        assert profile.gid is None
        assert profile.needs_explicit_id is False
        assert profile.image_family is None

    def test_umask_extraction(self):
        svc = _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                            env={"PUID": "1000", "PGID": "1000", "UMASK": "002"})
        profile = _build_permission_profile(svc)
        assert profile.umask == "002"

    def test_profile_to_dict(self):
        svc = _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                            env={"PUID": "1000", "PGID": "1000"})
        profile = _build_permission_profile(svc)
        d = profile.to_dict()
        assert d["uid"] == "1000"
        assert d["image_family"] == "LinuxServer.io"
        assert "service_name" in d


# ─── UMASK Normalization ───


class TestNormalizeUmask:
    """Tests for _normalize_umask()."""

    def test_three_digit(self):
        assert _normalize_umask("002") == "002"

    def test_four_digit(self):
        assert _normalize_umask("0002") == "002"

    def test_leading_zero_strip(self):
        assert _normalize_umask("022") == "022"

    def test_four_digit_022(self):
        assert _normalize_umask("0022") == "022"

    def test_quoted(self):
        assert _normalize_umask('"002"') == "002"
        assert _normalize_umask("'002'") == "002"

    def test_077(self):
        assert _normalize_umask("077") == "077"

    def test_invalid(self):
        result = _normalize_umask("abc")
        assert result == "abc"  # Returns as-is

    def test_two_digit(self):
        assert _normalize_umask("22") == "022"


# ─── Check: PUID/PGID Mismatch ───


class TestCheckPuidPgidMismatch:
    """Tests for _check_puid_pgid_mismatch()."""

    def test_mismatch_detected(self):
        """sonarr UID 1000, qbit defaults to 911 → conflict."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={}),
        ]
        conflicts = _check_puid_pgid_mismatch(services)
        assert len(conflicts) == 1
        assert conflicts[0].severity == "high"
        assert conflicts[0].conflict_type == "puid_pgid_mismatch"
        assert "qbittorrent" in conflicts[0].description

    def test_no_mismatch_when_all_match(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        assert _check_puid_pgid_mismatch(services) == []

    def test_mismatch_mixed_families(self):
        """LSIO sonarr (1000) + jlesage jdownloader (568) → conflict."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("jdownloader2", "jlesage/jdownloader-2",
                          role="download_client",
                          env={"USER_ID": "568", "GROUP_ID": "568"}),
        ]
        conflicts = _check_puid_pgid_mismatch(services)
        assert len(conflicts) == 1
        assert "568" in conflicts[0].detail

    def test_mismatch_compose_user_vs_env(self):
        """user: 1000:1000 vs PUID=500."""
        services = [
            _make_service("sabnzbd", "lscr.io/linuxserver/sabnzbd",
                          role="download_client",
                          compose_user="1000:1000"),
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "500", "PGID": "500"}),
        ]
        conflicts = _check_puid_pgid_mismatch(services)
        assert len(conflicts) == 1

    def test_single_service_no_conflict(self):
        """Single service can't have a mismatch."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        assert _check_puid_pgid_mismatch(services) == []

    def test_all_unknown_no_conflict(self):
        """If we can't determine any UIDs, no conflict."""
        services = [
            _make_service("app1", "custom/app1", env={}),
            _make_service("app2", "custom/app2", role="download_client", env={}),
        ]
        assert _check_puid_pgid_mismatch(services) == []

    def test_majority_identification(self):
        """With 3 services at 1000 and 1 at 911, majority should be 1000."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("plex", "lscr.io/linuxserver/plex",
                          role="media_server",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={}),  # defaults to 911
        ]
        conflicts = _check_puid_pgid_mismatch(services)
        assert len(conflicts) == 1
        # qbittorrent should be the outlier, not the 3 services at 1000
        assert "qbittorrent" in conflicts[0].description


# ─── Check: Missing PUID/PGID ───


class TestCheckMissingPuidPgid:
    """Tests for _check_missing_puid_pgid()."""

    def test_missing_detected(self):
        """LSIO qbit with no PUID/PGID → flagged."""
        services = [
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"UMASK": "002"}),
        ]
        conflicts = _check_missing_puid_pgid(services)
        assert len(conflicts) == 1
        assert conflicts[0].severity == "medium"
        assert conflicts[0].conflict_type == "missing_puid_pgid"
        assert "PUID" in conflicts[0].detail

    def test_not_flagged_for_unknown_image(self):
        """Unknown image with no env → NOT flagged."""
        services = [
            _make_service("myapp", "custom/myapp:latest",
                          role="download_client", env={}),
        ]
        assert _check_missing_puid_pgid(services) == []

    def test_not_flagged_when_compose_user_set(self):
        """LSIO image with user: directive → NOT flagged."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={}, compose_user="1000:1000"),
        ]
        assert _check_missing_puid_pgid(services) == []

    def test_not_flagged_when_puid_set(self):
        """LSIO image with PUID/PGID → NOT flagged."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        assert _check_missing_puid_pgid(services) == []

    def test_partial_missing_gid_only(self):
        """LSIO image with PUID but no PGID → flagged."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000"}),
        ]
        conflicts = _check_missing_puid_pgid(services)
        assert len(conflicts) == 1
        assert "PGID" in conflicts[0].detail

    def test_seerr_not_flagged(self):
        """Seerr family doesn't need PUID → NOT flagged."""
        services = [
            _make_service("overseerr", "sctx/overseerr:latest",
                          role="media_server", env={}),
        ]
        assert _check_missing_puid_pgid(services) == []

    def test_multiple_missing(self):
        """Multiple services missing PUID → single conflict listing all."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr", env={}),
            _make_service("radarr", "lscr.io/linuxserver/radarr", env={}),
        ]
        conflicts = _check_missing_puid_pgid(services)
        assert len(conflicts) == 1
        assert len(conflicts[0].services) == 2


# ─── Check: Root Execution ───


class TestCheckRootExecution:
    """Tests for _check_root_execution()."""

    def test_root_detected_via_env(self):
        services = [
            _make_service("huntarr", "lscr.io/linuxserver/huntarr",
                          env={"PUID": "0", "PGID": "0"}),
        ]
        conflicts = _check_root_execution(services)
        assert len(conflicts) == 1
        assert conflicts[0].severity == "medium"
        assert "root" in conflicts[0].description.lower()

    def test_root_detected_via_compose_user(self):
        services = [
            _make_service("app", "lscr.io/linuxserver/sonarr",
                          compose_user="0:0"),
        ]
        conflicts = _check_root_execution(services)
        assert len(conflicts) == 1

    def test_no_root_normal_uid(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        assert _check_root_execution(services) == []

    def test_root_string(self):
        services = [
            _make_service("app", "lscr.io/linuxserver/sonarr",
                          compose_user="root"),
        ]
        conflicts = _check_root_execution(services)
        assert len(conflicts) == 1


# ─── Check: UMASK Consistency ───


class TestCheckUmaskConsistency:
    """Tests for _check_umask_consistency()."""

    def test_inconsistency_detected(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000", "UMASK": "022"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"PUID": "1000", "PGID": "1000", "UMASK": "002"}),
        ]
        conflicts = _check_umask_consistency(services)
        umask_types = [c.conflict_type for c in conflicts]
        assert "umask_inconsistent" in umask_types

    def test_consistent_no_conflict(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"UMASK": "002"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={"UMASK": "002"}),
        ]
        conflicts = _check_umask_consistency(services)
        assert not any(c.conflict_type == "umask_inconsistent" for c in conflicts)

    def test_no_umask_no_conflict(self):
        """No UMASK set anywhere → nothing to compare."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={"PUID": "1000"}),
        ]
        assert _check_umask_consistency(services) == []

    def test_restrictive_umask_flagged(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"UMASK": "077"}),
        ]
        conflicts = _check_umask_consistency(services)
        assert any(c.conflict_type == "umask_restrictive" for c in conflicts)

    def test_normalize_0002_equals_002(self):
        """0002 and 002 should be treated as the same value."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"UMASK": "0002"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={"UMASK": "002"}),
        ]
        conflicts = _check_umask_consistency(services)
        assert not any(c.conflict_type == "umask_inconsistent" for c in conflicts)


# ─── Check: TZ Mismatch ───


class TestTzMismatch:
    """Tests for _check_tz_mismatch()."""

    def test_tz_mismatch_detected(self):
        """3 services, 2 with America/New_York, 1 with Europe/London → conflict."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "Europe/London"}),
        ]
        conflicts = _check_tz_mismatch(services)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "tz_mismatch"
        assert conflicts[0].severity == "low"
        assert conflicts[0].category == "B"
        assert "qbittorrent" in conflicts[0].description
        assert "Europe/London" in conflicts[0].description

    def test_matching_tz_no_conflict(self):
        """All services same TZ → no tz_mismatch."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"TZ": "America/New_York"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"TZ": "America/New_York"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"TZ": "America/New_York"}),
        ]
        assert _check_tz_mismatch(services) == []

    def test_missing_tz_not_flagged_as_mismatch(self):
        """Services with no TZ at all → no tz_mismatch (absence != mismatch)."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        assert _check_tz_mismatch(services) == []

    def test_tz_mismatch_fix_text(self):
        """Fix text should mention the majority TZ value."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service("radarr", "lscr.io/linuxserver/radarr",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"PUID": "1000", "PGID": "1000", "TZ": "Europe/London"}),
        ]
        conflicts = _check_tz_mismatch(services)
        _generate_fixes(conflicts, services)
        assert conflicts[0].fix is not None
        assert "America/New_York" in conflicts[0].fix
        assert "qbittorrent" in conflicts[0].fix


# ─── Orchestrator: _check_permissions() ───


class TestCheckPermissions:
    """Tests for the _check_permissions() orchestrator."""

    def test_healthy_no_conflicts(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000", "UMASK": "002"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client",
                          env={"PUID": "1000", "PGID": "1000", "UMASK": "002"}),
        ]
        conflicts = _check_permissions(services)
        assert len(conflicts) == 0

    def test_skips_non_participants(self):
        """Services with role 'other' should be ignored."""
        services = [
            _make_service("watchtower", "containrrr/watchtower",
                          role="other", env={}),
        ]
        assert _check_permissions(services) == []

    def test_multiple_issues(self):
        """A stack can have both mismatch AND root AND UMASK issues."""
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000", "UMASK": "022"}),
            _make_service("huntarr", "lscr.io/linuxserver/huntarr",
                          env={"PUID": "0", "PGID": "0", "UMASK": "002"}),
        ]
        conflicts = _check_permissions(services)
        types = {c.conflict_type for c in conflicts}
        assert "puid_pgid_mismatch" in types
        assert "root_execution" in types
        assert "umask_inconsistent" in types


# ─── Fix Generation ───


class TestFixGeneration:
    """Tests for permission-related fix text generators."""

    def test_puid_mismatch_fix(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={}),
        ]
        conflicts = _check_puid_pgid_mismatch(services)
        _generate_fixes(conflicts, services)
        assert conflicts[0].fix is not None
        assert "1000" in conflicts[0].fix
        assert "chown" in conflicts[0].fix

    def test_missing_puid_fix(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={}),
        ]
        conflicts = _check_missing_puid_pgid(services)
        _generate_fixes(conflicts, services)
        assert conflicts[0].fix is not None
        assert "PUID" in conflicts[0].fix
        assert "1000" in conflicts[0].fix  # Recommends majority value

    def test_root_fix(self):
        services = [
            _make_service("huntarr", "lscr.io/linuxserver/huntarr",
                          env={"PUID": "0", "PGID": "0"}),
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"PUID": "1000", "PGID": "1000"}),
        ]
        conflicts = _check_root_execution(services)
        _generate_fixes(conflicts, services)
        assert conflicts[0].fix is not None
        assert "root" in conflicts[0].fix.lower() or "0" in conflicts[0].fix

    def test_umask_fix(self):
        services = [
            _make_service("sonarr", "lscr.io/linuxserver/sonarr",
                          env={"UMASK": "022"}),
            _make_service("qbittorrent", "lscr.io/linuxserver/qbittorrent",
                          role="download_client", env={"UMASK": "002"}),
        ]
        conflicts = _check_umask_consistency(services)
        _generate_fixes(conflicts, services)
        umask_conflict = next(c for c in conflicts if c.conflict_type == "umask_inconsistent")
        assert umask_conflict.fix is not None
        assert "002" in umask_conflict.fix


# ─── Integration Tests ───


class TestIntegration:
    """Integration tests using resolve_compose + analyze_stack."""

    def test_puid_mismatch_full_analysis(self, make_stack):
        """Full analysis detects PUID mismatch via compose file."""
        from backend.analyzer import analyze_stack

        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - UMASK=002
                volumes:
                  - ./config/qbit:/config
                  - /mnt/nas/data:/data
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        conflict_types = [c.conflict_type for c in result.conflicts]
        assert "puid_pgid_mismatch" in conflict_types or "missing_puid_pgid" in conflict_types

    def test_healthy_perms_no_conflict(self, make_stack):
        """Stack with matching PUID/PGID has no permission conflicts."""
        from backend.analyzer import analyze_stack

        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - UMASK=002
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - UMASK=002
                volumes:
                  - ./config/qbit:/config
                  - /mnt/nas/data:/data
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        perm_types = {"puid_pgid_mismatch", "missing_puid_pgid", "root_execution",
                      "umask_inconsistent", "umask_restrictive", "cross_stack_puid_mismatch"}
        for c in result.conflicts:
            assert c.conflict_type not in perm_types

    def test_compose_user_extracted(self, make_stack):
        """compose user: directive is correctly extracted."""
        from backend.analyzer import analyze_stack

        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                user: "1000:1000"
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        sonarr = next(s for s in result.services if s.name == "sonarr")
        assert sonarr.compose_user is not None

    def test_permission_profiles_in_output(self, make_stack):
        """AnalysisResult.to_dict() includes permission_profiles."""
        from backend.analyzer import analyze_stack

        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        data = result.to_dict()
        assert "permission_profiles" in data
        assert len(data["permission_profiles"]) >= 1
        assert data["permission_profiles"][0]["uid"] == "1000"

    def test_steps_include_permissions(self, make_stack):
        """Analysis steps log should include permissions check."""
        from backend.analyzer import analyze_stack

        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                volumes:
                  - ./config:/config
                  - /mnt/nas/data:/data
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        step_texts = [s.get("text", "") for s in result.steps]
        assert any("ermission" in t for t in step_texts)  # "Permissions check passed" or "permission issue"
