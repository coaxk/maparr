"""Tests for CONFLICT_CATEGORIES mapping, Conflict.category property,
and Category D observations collection.

Verifies that every conflict type is mapped to a category letter (A-D),
that the Conflict dataclass exposes the category via a property, and that
_collect_observations produces informational items with no health impact.
"""

import pytest

from backend.analyzer import (
    CONFLICT_CATEGORIES,
    Conflict,
    ServiceInfo,
    _collect_observations,
    analyze_stack,
)
from backend.resolver import resolve_compose


# ─── Category mapping tests ───

class TestConflictCategories:
    """Test the CONFLICT_CATEGORIES constant dict."""

    CATEGORY_A_TYPES = [
        "no_shared_mount",
        "different_host_paths",
        "named_volume_data",
        "path_unreachable",
    ]

    CATEGORY_B_TYPES = [
        "puid_pgid_mismatch",
        "missing_puid_pgid",
        "root_execution",
        "umask_inconsistent",
        "umask_restrictive",
        "cross_stack_puid_mismatch",
        "tz_mismatch",
    ]

    CATEGORY_C_TYPES = [
        "wsl2_performance",
        "mixed_mount_types",
        "windows_path_in_compose",
        "remote_filesystem",
    ]

    CATEGORY_D_TYPES = [
        "missing_restart_policy",
        "latest_tag_usage",
        "missing_tz",
        "privileged_mode",
        "no_healthcheck",
    ]

    @pytest.mark.parametrize("conflict_type", CATEGORY_A_TYPES)
    def test_category_a_path_conflicts(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "A"

    @pytest.mark.parametrize("conflict_type", CATEGORY_B_TYPES)
    def test_category_b_permission_env(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "B"

    @pytest.mark.parametrize("conflict_type", CATEGORY_C_TYPES)
    def test_category_c_infrastructure(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "C"

    @pytest.mark.parametrize("conflict_type", CATEGORY_D_TYPES)
    def test_category_d_observations(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "D"

    def test_no_unknown_categories(self):
        """Every value in the dict must be A, B, C, or D."""
        valid = {"A", "B", "C", "D"}
        for conflict_type, category in CONFLICT_CATEGORIES.items():
            assert category in valid, f"{conflict_type} mapped to unknown category {category!r}"

    def test_all_expected_types_present(self):
        """Every type listed above must appear in the dict."""
        all_expected = (
            self.CATEGORY_A_TYPES
            + self.CATEGORY_B_TYPES
            + self.CATEGORY_C_TYPES
            + self.CATEGORY_D_TYPES
        )
        for t in all_expected:
            assert t in CONFLICT_CATEGORIES, f"Missing conflict type: {t}"


# ─── Conflict.category property tests ───

class TestConflictCategoryProperty:
    """Test the category property on the Conflict dataclass."""

    def _make_conflict(self, conflict_type: str) -> Conflict:
        return Conflict(
            conflict_type=conflict_type,
            severity="medium",
            services=["sonarr"],
            description="test",
        )

    def test_category_a(self):
        c = self._make_conflict("no_shared_mount")
        assert c.category == "A"

    def test_category_b(self):
        c = self._make_conflict("puid_pgid_mismatch")
        assert c.category == "B"

    def test_category_c(self):
        c = self._make_conflict("wsl2_performance")
        assert c.category == "C"

    def test_category_d(self):
        c = self._make_conflict("missing_restart_policy")
        assert c.category == "D"

    def test_unknown_type_returns_none(self):
        c = self._make_conflict("totally_made_up_type")
        assert c.category is None

    def test_category_in_to_dict(self):
        c = self._make_conflict("different_host_paths")
        d = c.to_dict()
        assert "category" in d
        assert d["category"] == "A"

    def test_category_none_in_to_dict(self):
        c = self._make_conflict("unknown_type")
        d = c.to_dict()
        assert "category" in d
        assert d["category"] is None


# ─── Category D: Observations collection tests ───

class TestObservations:
    """Tests for _collect_observations() — informational items with no health impact."""

    def test_latest_tag_observed(self, make_stack):
        """Services with :latest tag produce an observation."""
        compose = {
            "services": {
                "sonarr": {"image": "lscr.io/linuxserver/sonarr:latest"},
                "radarr": {"image": "lscr.io/linuxserver/radarr"},  # no tag = implicit latest
            }
        }
        services = [
            ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:latest", role="arr"),
            ServiceInfo(name="radarr", image="lscr.io/linuxserver/radarr", role="arr"),
        ]
        obs = _collect_observations(compose, services)
        latest_obs = [o for o in obs if o["type"] == "latest_tag_usage"]
        assert len(latest_obs) == 2
        names = {o["service"] for o in latest_obs}
        assert names == {"sonarr", "radarr"}

    def test_pinned_tag_no_observation(self):
        """Services with a pinned tag do NOT produce latest_tag_usage."""
        compose = {
            "services": {
                "sonarr": {"image": "lscr.io/linuxserver/sonarr:4.0.2", "restart": "unless-stopped"},
            }
        }
        services = [ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:4.0.2", role="arr")]
        obs = _collect_observations(compose, services)
        latest_obs = [o for o in obs if o["type"] == "latest_tag_usage"]
        assert len(latest_obs) == 0

    def test_missing_restart_policy(self, make_stack):
        """Services without restart policy produce an observation."""
        compose = {
            "services": {
                "sonarr": {"image": "lscr.io/linuxserver/sonarr:4.0.2"},
                "radarr": {"image": "lscr.io/linuxserver/radarr:4.0.2", "restart": "unless-stopped"},
            }
        }
        services = [
            ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:4.0.2", role="arr"),
            ServiceInfo(name="radarr", image="lscr.io/linuxserver/radarr:4.0.2", role="arr"),
        ]
        obs = _collect_observations(compose, services)
        restart_obs = [o for o in obs if o["type"] == "missing_restart_policy"]
        # sonarr has no restart, radarr does
        assert len(restart_obs) == 1
        assert restart_obs[0]["service"] == "sonarr"

    def test_privileged_mode_observed(self, make_stack):
        """Privileged services produce an observation."""
        compose = {
            "services": {
                "vpn": {"image": "gluetun:latest", "privileged": True, "restart": "always"},
            }
        }
        services = [ServiceInfo(name="vpn", image="gluetun:latest", role="utility")]
        obs = _collect_observations(compose, services)
        priv_obs = [o for o in obs if o["type"] == "privileged_mode"]
        assert len(priv_obs) == 1
        assert priv_obs[0]["service"] == "vpn"

    def test_privileged_false_no_observation(self):
        """privileged: false should NOT produce an observation."""
        compose = {
            "services": {
                "vpn": {"image": "gluetun:3.0", "privileged": False, "restart": "always"},
            }
        }
        services = [ServiceInfo(name="vpn", image="gluetun:3.0", role="utility")]
        obs = _collect_observations(compose, services)
        priv_obs = [o for o in obs if o["type"] == "privileged_mode"]
        assert len(priv_obs) == 0

    def test_missing_tz_on_media_service(self, make_stack):
        """Media services without TZ produce an observation."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "lscr.io/linuxserver/sonarr:4.0.2",
                    "restart": "unless-stopped",
                    "environment": {"PUID": "1000", "PGID": "1000"},
                },
            }
        }
        services = [ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:4.0.2", role="arr")]
        obs = _collect_observations(compose, services)
        tz_obs = [o for o in obs if o["type"] == "missing_tz"]
        assert len(tz_obs) == 1
        assert tz_obs[0]["service"] == "sonarr"

    def test_missing_tz_not_on_non_media(self, make_stack):
        """Non-media services without TZ do NOT produce missing_tz."""
        compose = {
            "services": {
                "watchtower": {
                    "image": "containrrr/watchtower:1.5",
                    "restart": "unless-stopped",
                },
            }
        }
        services = [ServiceInfo(name="watchtower", image="containrrr/watchtower:1.5", role="utility")]
        obs = _collect_observations(compose, services)
        tz_obs = [o for o in obs if o["type"] == "missing_tz"]
        assert len(tz_obs) == 0

    def test_tz_present_no_observation(self):
        """Media service WITH TZ set should NOT produce missing_tz."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "lscr.io/linuxserver/sonarr:4.0.2",
                    "restart": "unless-stopped",
                    "environment": {"PUID": "1000", "PGID": "1000", "TZ": "America/New_York"},
                },
            }
        }
        services = [ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:4.0.2", role="arr")]
        obs = _collect_observations(compose, services)
        tz_obs = [o for o in obs if o["type"] == "missing_tz"]
        assert len(tz_obs) == 0

    def test_tz_in_list_format(self):
        """TZ set via list-format environment should not trigger missing_tz."""
        compose = {
            "services": {
                "sonarr": {
                    "image": "lscr.io/linuxserver/sonarr:4.0.2",
                    "restart": "unless-stopped",
                    "environment": ["PUID=1000", "PGID=1000", "TZ=Europe/London"],
                },
            }
        }
        services = [ServiceInfo(name="sonarr", image="lscr.io/linuxserver/sonarr:4.0.2", role="arr")]
        obs = _collect_observations(compose, services)
        tz_obs = [o for o in obs if o["type"] == "missing_tz"]
        assert len(tz_obs) == 0

    def test_observations_have_no_health_impact(self, make_stack):
        """Stack with only observations should still be healthy."""
        stack_path = make_stack("""\
            services:
              sonarr:
                image: lscr.io/linuxserver/sonarr:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data/media:/media
                  - /data/downloads:/downloads
              qbittorrent:
                image: lscr.io/linuxserver/qbittorrent:latest
                environment:
                  - PUID=1000
                  - PGID=1000
                  - TZ=America/New_York
                volumes:
                  - /data/media:/media
                  - /data/downloads:/downloads
        """)
        resolved = resolve_compose(stack_path)
        result = analyze_stack(
            resolved_compose=resolved,
            stack_path=stack_path,
            compose_file=resolved["_compose_file"],
            resolution_method=resolved["_resolution"],
        )
        d = result.to_dict()
        # Should be healthy — no conflicts
        assert d["status"] == "healthy"
        # But should have observations (latest tag, missing restart)
        assert len(result.observations) > 0
        obs_types = {o["type"] for o in result.observations}
        assert "latest_tag_usage" in obs_types
        assert "missing_restart_policy" in obs_types
        # Observations should appear in to_dict output
        assert "observations" in d
        assert len(d["observations"]) > 0
